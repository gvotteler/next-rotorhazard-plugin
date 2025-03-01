import logging
from eventmanager import Evt
from Database import Pilot, Profiles
import json
import RHAPI
import requests
from requests.exceptions import RequestException
from RHUI import UIField, UIFieldType, UIFieldSelectOption
import time

logger = logging.getLogger(__name__)

class NextIntegration: 
    def __init__(self, rhapi:RHAPI.RHAPI):
        self._rhapi = rhapi
        self._session = requests.Session()  # Reutilizar sesión HTTP para reducir sobrecarga
        self._last_sent_data = {}  # Caché para evitar envíos duplicados
        self._next_url = None  # Se inicializará cuando sea necesario
        self._next_enabled = False  # Se verificará una sola vez

        # Registrar eventos con manejo eficiente
        events_to_register = [
            (Evt.HEAT_SET, self.onHeatChange),
            (Evt.LAPS_SAVE, self.raceSave),
            (Evt.RACE_LAP_RECORDED, self.raceProgress),
            (Evt.LAPS_RESAVE, self.on_laps_resave)
        ]
        
        for event, handler in events_to_register:
            rhapi.events.on(event, handler)
        
        # Configurar UI
        rhapi.ui.register_panel("next_format", "Next", "format")
        rhapi.fields.register_option(UIField('next_status', 'Turn On service', field_type=UIFieldType.CHECKBOX), 'next_format')
        rhapi.fields.register_option(UIField('next_ip', "IP Next Server", UIFieldType.TEXT), 'next_format')
        rhapi.fields.register_option(UIField('next_event_id', "Next Event Id", UIFieldType.TEXT), 'next_format')
        rhapi.ui.register_quickbutton('next_format', 'import_pilots', 'Import pilots', self.importPilots)

    def _check_enabled(self):
        """Verificar si el servicio está habilitado y actualizar URL base"""
        status = self._rhapi.db.option("next_status") == "1"
        
        if status and self._next_url is None:
            # Solo actualizar URL si es necesario
            ip = self._rhapi.db.option("next_ip")
            if ip:
                self._next_url = f"http://{ip}"
            else:
                status = False  # Desactivar si no hay IP configurada
        
        self._next_enabled = status
        return status

    def _send_request(self, endpoint, data, retries=2):
        """Enviar solicitud HTTP con manejo de errores y reintentos"""
        if not self._check_enabled() or not self._next_url:
            return None
            
        url = f"{self._next_url}/{endpoint}"
        
        # Añadir nextId a todos los datos
        if isinstance(data, dict):
            data["nextId"] = self._rhapi.db.option("next_event_id")
        
        # Calcular hash para verificar si ya enviamos estos mismos datos
        data_hash = str(hash(json.dumps(data, sort_keys=True)))
        if endpoint in self._last_sent_data and self._last_sent_data[endpoint] == data_hash:
            logger.debug(f"Skipping duplicate data for {endpoint}")
            return None
            
        for attempt in range(retries + 1):
            try:
                response = self._session.post(url, json=data, timeout=5)
                response.raise_for_status()
                self._last_sent_data[endpoint] = data_hash
                return response.json()
            except RequestException as e:
                if attempt < retries:
                    time.sleep(0.5)  # Espera breve entre reintentos
                else:
                    logger.warning(f"Failed to send data to {endpoint}: {str(e)}")
                    return None

    def importPilots(self, args):
        """Importar pilotos desde Next"""
        if not self._check_enabled():
            return
            
        self._rhapi.ui.message_notify(self._rhapi.language.__("Next - Pilot importing starts"))
        
        data = {'nextId': self._rhapi.db.option("next_event_id")}
        response_data = self._send_request("data/import_pilots", data)
        
        if response_data and "pilots" in response_data:
            for pilot_name in response_data["pilots"]:                
                self._rhapi.db.pilot_add(name=pilot_name, callsign=pilot_name)
            self._rhapi.ui.message_notify(self._rhapi.language.__("Next - Pilot importing finished"))

    def onHeatChange(self, args):
        """Manejar cambio de heat"""
        if not self._check_enabled():
            return
            
        currentRound = self._rhapi.race.round
        currentHeat = self._rhapi.race.heat
        race_id = f"{currentHeat}{currentRound + 1}"
        
        try:
            # Obtener datos de frecuencia
            frequencyset = self._rhapi.race.frequencyset
            frequencies = json.loads(frequencyset.frequencies)
            slots_bands = frequencies["b"]
            slots_channels = frequencies["c"]
            slots_frequencies = frequencies["f"]
            
            # Obtener pilotos
            pilots = self._rhapi.race.pilots
            
            payload = []
            for slot_index, pilot_id in pilots.items():
                if pilot_id != 0:
                    pilot = self._rhapi.db.pilot_by_id(pilot_id)
                    if pilot:
                        payload.append({
                            "callsign": pilot.callsign,
                            "band": slots_bands[slot_index],
                            "channel": slots_channels[slot_index],
                            "frequency": slots_frequencies[slot_index],
                            "heat": race_id
                        })
            
            if payload:  # Solo enviar si hay datos
                self._send_request("data/pilots", {'data': payload})
        except Exception as e:
            logger.warning(f"Error processing heat change: {str(e)}")

    def raceSave(self, args):
        """Manejar guardado de carrera"""
        if not self._check_enabled():
            return
            
        try:
            currentRound = self._rhapi.race.round
            currentHeat = self._rhapi.race.heat
            raceId = f"{currentHeat}{currentRound}"
            
            data = self._rhapi.race.results
            pilots_vector = []
            
            for pilot in data.get("by_consecutives", []):
                # Usar dict comprehension para mayor eficiencia
                pilot_data = {k: pilot.get(k) for k in [
                    "callsign", "laps", "total_time", "total_time_laps",
                    "average_lap", "fastest_lap", "consecutives", "position"
                ]}
                pilots_vector.append(pilot_data)
            
            if pilots_vector:  # Solo enviar si hay datos
                self._send_request("data/heat_data", {
                    'pilots_vector': pilots_vector,
                    'race_id': raceId
                })
        except Exception as e:
            logger.warning(f"Error processing race save: {str(e)}")
            
    def raceProgress(self, args):
        """Manejar progreso de carrera"""
        if not self._check_enabled():
            return
            
        try:
            parsed_data = args
            result_vector = []
            heat_id = "00"
            
            for obj in parsed_data["results"]["by_race_time"]:
                fastest_lap_source = obj.get("fastest_lap_source", {})
                
                if isinstance(fastest_lap_source, dict):
                    heat_info = f"heat: {fastest_lap_source.get('heat')}, round: {fastest_lap_source.get('round')}"
                else:
                    heat_info = "00"
                
                result_vector.append({
                    "callsign": obj["callsign"],
                    "laps": obj["laps"],
                    "last_lap": obj["last_lap"],
                    "position": obj["position"],
                    "heatId": heat_info
                })
            
            if result_vector:  # Solo enviar si hay datos
                self._send_request("data/laps_data", {
                    'pilots_vector': result_vector,
                    'heat_id': heat_id
                })
        except Exception as e:
            logger.warning(f"Error processing race progress: {str(e)}")

    def on_laps_resave(self, args):
        """Manejar re-guardado de vueltas"""
        if not self._check_enabled():
            return
            
        # Reducir tiempo de espera a 1 segundo en lugar de 2
        time.sleep(1)
        self.raceResave(args)

    def raceResave(self, args):
        """Procesar datos de re-guardado"""
        if not self._check_enabled():
            return
            
        try:
            race_id = args.get('race_id')
            laps_raw = self._rhapi.db.race_by_id(race_id)
            
            if hasattr(laps_raw, "__dict__"):
                laps_raw = vars(laps_raw)
                
            results = laps_raw.get("results", {})
            pilots_vector = []
            round_heat_concat = ""
            
            if isinstance(results, dict) and "by_consecutives" in results:
                for pilot in results["by_consecutives"]:
                    consecutives_source = pilot.get("consecutives_source")
                    if consecutives_source:
                        round_num = consecutives_source.get("round", 0) + 1
                        heat = consecutives_source.get("heat", 0)
                        round_heat_concat = f"{heat}{round_num}"
                    
                    # Usar dict comprehension para mayor eficiencia
                    pilot_data = {k: pilot.get(k) for k in [
                        "callsign", "laps", "total_time", "total_time_laps",
                        "average_lap", "fastest_lap", "consecutives", "position"
                    ]}
                    pilots_vector.append(pilot_data)
            
            if pilots_vector:  # Solo enviar si hay datos
                self._send_request("data/resave_data", {
                    'pilots_vector': pilots_vector,
                    'heat_id': round_heat_concat
                })
        except Exception as e:
            logger.warning(f"Error processing race resave: {str(e)}")

def initialize(rhapi):
    NextIntegration(rhapi)
