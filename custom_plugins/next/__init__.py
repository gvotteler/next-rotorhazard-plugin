# server/plugins/next_connector/__init__.py
# -*- coding: utf-8 -*-
"""
Next Connector (Socket.IO only)
- Emite telemetría (RSSI / Peak / Freq) vía 'next_heartbeat'
- Entrega snapshot de nodos vía 'next_nodes' o 'next_get_nodes' (ack)
- Expone acciones: iniciar (stage), detener (stop), terminar (stop+save), abortar (clear)
- Re-emite resultados y progreso en vivo vía 'next_race_results', 'next_live_update', 'next_resave_data'
- Permite setear pilotos y frecuencias vía 'next_set_pilots' y 'next_set_frequencies'
Compatible con Python 3.9+
"""

import json
import time
import logging
from eventmanager import Evt
from RHUtils import HEAT_ID_NONE
from RHRace import RaceStatus

logger = logging.getLogger(__name__)

class NextConnector:
    def __init__(self, rhapi):
        self._rhapi = rhapi
        self._peaks = {}     # peak RSSI por seat
        self._last_nodes = []  # último snapshot de nodos

    def initialize(self, _args):
        logger.info('Initializing Next Connector (Socket.IO only)')

        # Escuchar pedidos desde Next (Socket.IO)
        ui = self._rhapi.ui
        ui.socket_listen('next_ping', lambda _=None: {"pong": True})

        ui.socket_listen('next_get_nodes', self._on_get_nodes)
        ui.socket_listen('next_reset_peaks', self._on_reset_peaks)

        ui.socket_listen('next_set_frequencies', self.next_set_frequencies)

        # Eventos internos de RH → emitir a Next
        ev = self._rhapi.events
        # Heartbeat: RSSI / crossing (si disponible en tu build)
        try:
            ev.on(Evt.HEARTBEAT, self.on_heartbeat)
        except Exception as e:
            logger.warning("HEARTBEAT event not available on this RH build: %s", e)
        # Cambios de heat → snapshot de nodos
        ev.on(Evt.HEAT_SET, self._emit_nodes_snapshot)  
        

    # -------------------- TELEMETRÍA / SNAPSHOTS --------------------

    def _get_nodes_snapshot(self):
        snap = {"nodes": []}
        try:
            fset = self._rhapi.race.frequencyset
            freqs = fset.frequencies
            if isinstance(freqs, str):
                freqs = json.loads(freqs)
            b = freqs.get('b', []) or []
            c = freqs.get('c', []) or []
            f = freqs.get('f', []) or []
            n = max(len(b), len(c), len(f))
            for i in range(n):
                snap["nodes"].append({
                    "seat": i,
                    "band": b[i] if i < len(b) else None,
                    "channel": c[i] if i < len(c) else None,
                    "frequency": f[i] if i < len(f) else None
                })
        except Exception as e:
            logger.warning("nodes snapshot error: %s", e)
        self._last_nodes = snap["nodes"]
        return snap

    def _emit_nodes_snapshot(self, *_args, **_kwargs):
        self._rhapi.ui.socket_broadcast('next_nodes', self._get_nodes_snapshot())

    def _on_get_nodes(self, _=None):

        return self._get_nodes_snapshot()

    def _on_reset_peaks(self, _=None):
        self._peaks.clear()
        return {"ok": True}

    def on_heartbeat(self, hb):
        try:
            rssi = hb.get('current_rssi') or hb.get('rssi') or []
            crossing = hb.get('crossing_flag') or []

            # Frecuencias por seat (desde último snapshot, o recalcular si está vacío)
            if not self._last_nodes:
                self._last_nodes = self._get_nodes_snapshot().get("nodes", [])
            freqs = [n.get("frequency") for n in self._last_nodes]

            # Peak por seat
            peaks = []
            for i, val in enumerate(rssi):
                try:
                    v = float(val) if val is not None else None
                except Exception:
                    v = None
                prev = self._peaks.get(i)
                if v is not None and (prev is None or v > prev):
                    self._peaks[i] = v
                peaks.append(self._peaks.get(i))

            payload = {
                "rssi": rssi,
                "peak": peaks,
                "frequency": freqs,
                "crossing_flag": crossing,
                "server_time_s": time.time()
            }
            self._rhapi.ui.socket_broadcast('next_heartbeat', payload)
        except Exception as e:
            logger.debug("heartbeat parse error: %s", e)

    
    def _get_frequencyset_by_id(self, fs_id: int):

        db = self._rhapi.db
        # Algunos builds traen helper:
        try:
            return db.frequencyset_by_id(fs_id)
        except Exception:
            pass
        # Fallback: iterar la colección
        try:
            for item in getattr(db, "frequencysets", []):
                if getattr(item, "id", None) == fs_id:
                    return item
        except Exception:
            pass
        return None

    def next_set_frequencies(self, data):
        logger.info("[Next] next_set_frequencies (default profile=1) payload=%s", data)
        try:
            # 1) Normalizar arrays
            b = list(data.get('b') or [])
            c = list(data.get('c') or [])
            f_in = list(data.get('f') or [])

            f = []
            for x in f_in:
                if x is None or str(x).strip() == "":
                    f.append(None)
                else:
                    try:
                        f.append(int(x))
                    except Exception:
                        f.append(int(float(x)))  # admite "5732.0"

            n = max(len(b), len(c), len(f))
            if n == 0:
                return {"ok": False, "error": "payload vacío (b/c/f)"}

            b += [None] * (n - len(b))
            c += [None] * (n - len(c))
            f += [None] * (n - len(f))
            freqs = {"b": b, "c": c, "f": f}

            # 2) Forzar profile por defecto (id=1)
            DEFAULT_FS_ID = 1
            fset = self._get_frequencyset_by_id(DEFAULT_FS_ID)
            if not fset:
                # Si de verdad no existe, mejor devolvemos error explícito
                logger.error("[Next] Frequency set id=1 no existe.")
                return {"ok": False, "error": "Default frequency set (id=1) no existe en este timer"}

            # 3) Guardar frecuencias en el set 1
            try:
                self._rhapi.db.frequencyset_alter(fset.id, frequencies=freqs)
            except TypeError:
                self._rhapi.db.frequencyset_alter(fset.id, frequencies=json.dumps(freqs))

            # 4) Asegurar que la carrera usa el set 1
            try:
                self._rhapi.race.frequencyset = fset.id
            except Exception:
                pass

            # 5) Notificar UIs & clientes
            try:
                self._rhapi.ui.broadcast_heats()
                self._rhapi.ui.broadcast_current_heat()
            except Exception:
                pass
            self._emit_nodes_snapshot()  # emite 'next_nodes' con snapshot actualizado

            logger.info("[Next] Frequencies applied on default set id=1 (seats=%d)", n)
            return {"ok": True, "count": n, "profile": {"id": 1, "name": getattr(fset, 'name', None)}}
        except Exception as e:
            logger.exception("[Next] next_set_frequencies failed")
            return {"ok": False, "error": str(e)}

# Hook de carga del plugin
def initialize(rhapi):
    connector = NextConnector(rhapi)
    rhapi.events.on(Evt.STARTUP, connector.initialize)
