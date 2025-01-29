import logging
from eventmanager import Evt
from Database import Pilot, Profiles
import json
import RHAPI
import requests
from RHUI import UIField, UIFieldType, UIFieldSelectOption


logger = logging.getLogger(__name__)

class heatSender: 
    current_round = 1
    
    def __init__(self, rhapi:RHAPI.RHAPI):
        # Store the RHAPI object to allow connections in the future 
        self._rhapi = rhapi

        # Setup an event for when the active heat changes
        # Avaliable events can be found at https://github.com/RotorHazard/RotorHazard/blob/264d39a01c2a391c4ed059d84c5f21969f14dae9/src/server/eventmanager.py#L100
        self._rhapi.events.on(Evt.HEAT_SET, self.onHeatChange)
        self._rhapi.events.on(Evt.LAPS_SAVE, self.raceSave)
        self._rhapi.events.on(Evt.RACE_LAP_RECORDED, self.raceProgress)
        rhapi.ui.register_panel("next_format", "Next", "format")
        self._rhapi.events.on(Evt.LAPS_RESAVE, self.raceResave)

        rhapi.fields.register_option( UIField('next_status', 'Turn On service', field_type = UIFieldType.CHECKBOX), 'next_format'  )
        rhapi.fields.register_option( UIField('next_ip', "IP Next Server", UIFieldType.TEXT), 'next_format' )
        rhapi.fields.register_option( UIField('next_event_id', "Next Event Id", UIFieldType.TEXT), 'next_format')
        rhapi.ui.register_quickbutton('next_format', 'import_pilots', 'Import pilots', self.importPilots)

    def importPilots(self, args):
        self._rhapi.ui.message_notify(self._rhapi.language.__("Next - Pilot importing starts"))
        
        if (self._rhapi.db.option("next_status") == "1"): 
            data = {
                    'nextId': self._rhapi.db.option("next_event_id"),
                } 
            response = requests.post('http://' + self._rhapi.db.option("next_ip") + "/data/import_pilots", json=data) 
            response_data = response.json()

            logger.info("Pilots to import: %s", response_data)        

            for pilot_name in response_data["pilots"]:                
                self._rhapi.db.pilot_add(name=pilot_name, callsign=pilot_name)
            self._rhapi.ui.message_notify(self._rhapi.language.__("Next - Pilot importing finished"))
       

    def onHeatChange(self, args):
        currentRound = self._rhapi.race.round
        currentHeat = self._rhapi.race.heat
        race_id = str(currentHeat) + str(currentRound + 1)
        logger.info("Race on change: %s", race_id)

        if (self._rhapi.db.option("next_status") == "1"):
                # Since you only care about the active race, we can ignore the heat_id and utalize the rhapi.race api.
                # This connection only provides data for the currently active race
                # https://github.com/RotorHazard/RotorHazard/blob/main/doc/RHAPI.md#active-race

                # Gets the currently active frequency set
                # https://github.com/RotorHazard/RotorHazard/blob/main/doc/RHAPI.md#racefrequencyset
                frequencyset:Profiles = self._rhapi.race.frequencyset

                # Get the frequencies for the set. The different list should be loaded in used the json module:
                # "b" : access to the list of bands
                # "c" : access to the list of channels
                # "f" : access to the list of frequencies
                frequencies:dict[str,list]  = json.loads(frequencyset.frequencies)
                slots_bands:list[str]       = frequencies["b"]
                slots_channels:list[int]    = frequencies["c"]
                slots_frequencies:list[int] = frequencies["f"]

                # This variable provides a dictionary of in the format of {slot_index : pilot_id} 
                # https://github.com/RotorHazard/RotorHazard/blob/main/doc/RHAPI.md#racepilots
                pilots:dict[int,int] = self._rhapi.race.pilots
                
                payload = [] 
            
                # Loop through each slot and grab the callsign and frequency info for the pilot (if there is one)
                for slot_index, pilot_id in pilots.items():
                        
                    if pilot_id != 0:
                        pilot:Pilot = self._rhapi.db.pilot_by_id(pilot_id)

                        data_dic = {
                                "callsign":pilot.callsign,
                                "band":slots_bands[slot_index],
                                "channel":slots_channels[slot_index],
                                "frequency":slots_frequencies[slot_index],
                                "heat":race_id
                        }
                        
                        payload.append(data_dic)
                        #logger.info(data_dic)
                race_data = {
                    'data': payload,
                    "nextId": self._rhapi.db.option("next_event_id")
                } 
                #logger.info("Next push data:" + self._rhapi.db.option("next_ip") + "/v1/next/data/pilots")
                requests.post('http://' + self._rhapi.db.option("next_ip") + "/data/pilots", json=race_data)
        
    def raceSave(self, args):
        if (self._rhapi.db.option("next_status") == "1"):
        
                currentRound = self._rhapi.race.round
                currentHeat = self._rhapi.race.heat
               
                raceId = str(currentHeat) + str(currentRound)
                logger.info("Save race Id: %s", raceId)

                data = self._rhapi.race.results
               
                # Extraer datos deseados
                pilots_vector = []
                for pilot in data.get("by_consecutives", []):
                    pilot_data = {
                        "callsign": pilot.get("callsign"),
                        "laps": pilot.get("laps"),
                        "total_time": pilot.get("total_time"),
                        "total_time_laps": pilot.get("total_time_laps"),
                        "average_lap": pilot.get("average_lap"),
                        "fastest_lap": pilot.get("fastest_lap"),
                        "consecutives": pilot.get("consecutives"),
                        "position": pilot.get("position")
                    }
                    pilots_vector.append(pilot_data)

                # Crear un nuevo array con las claves 'pilots_vector' y 'race_id'
                race_data = {
                    'pilots_vector': pilots_vector,
                    'race_id': raceId,
                    "nextId": self._rhapi.db.option("next_event_id")
                }            

                #logger.info("Heat laps: %s", pilots_vector)    
                requests.post('http://' + self._rhapi.db.option("next_ip") + "/data/heat_data", json=race_data)     
           
    def to_dict(self, obj):
 
        if hasattr(obj, '__dict__'):
            return {
                k: self.to_dict(v) if isinstance(v, (list, dict)) else v
                for k, v in obj.__dict__.items() if not k.startswith('_')
            }
        return obj  
      
    def raceProgress(self, args):

         if (self._rhapi.db.option("next_status") == "1"):  
            parsed_data = args
            logger.info("Heat laps: %s", args)  
            result_vector = []
            heat_id = "00"
            for obj in parsed_data["results"]["by_race_time"]:
                fastest_lap_source = obj.get("fastest_lap_source", {})
                # Concatenar heat y round si están disponibles
                heatId = (
                    f"heat: {fastest_lap_source.get('heat')}, round: {fastest_lap_source.get('round')}"
                    if isinstance(fastest_lap_source, dict)
                    else "00"
                )
                
                result_vector.append({
                    "callsign": obj["callsign"],
                    "laps": obj["laps"],
                    "last_lap": obj["last_lap"],
                    "position": obj["position"],
                    "heatId": heatId
                })
                heat_id = heat_id

            laps_data = {
                'pilots_vector': result_vector,
                'heat_id': heat_id,
                'nextId': self._rhapi.db.option("next_event_id")
            }   

            logger.info("Heat laps: %s", laps_data)        
            requests.post('http://' + self._rhapi.db.option("next_ip") + "/data/laps_data", json=laps_data)    


    def raceResave(self, args):
        if (self._rhapi.db.option("next_status") == "1"):
        
                currentRound = self._rhapi.race.round
                currentHeat = self._rhapi.race.heat
                logger.info("Heat laps: %s", args)  
                logger.info("1: %s", currentRound)    
                logger.info("2: %s", currentHeat)        

               
                raceId = str(currentHeat) + str(currentRound)
                logger.info("Save race Id: %s", raceId)

                data = self._rhapi.race.results
               
                # Extraer datos deseados
                pilots_vector = []
                for pilot in data.get("by_consecutives", []):
                    pilot_data = {
                        "callsign": pilot.get("callsign"),
                        "laps": pilot.get("laps"),
                        "total_time": pilot.get("total_time"),
                        "total_time_laps": pilot.get("total_time_laps"),
                        "average_lap": pilot.get("average_lap"),
                        "fastest_lap": pilot.get("fastest_lap"),
                        "consecutives": pilot.get("consecutives"),
                        "position": pilot.get("position")
                    }
                    pilots_vector.append(pilot_data)

                # Crear un nuevo array con las claves 'pilots_vector' y 'race_id'
                race_data = {
                    'pilots_vector': pilots_vector,
                    'race_id': raceId,
                    "nextId": self._rhapi.db.option("next_event_id")
                }            

                logger.info("Heat laps: %s", pilots_vector)    
                #requests.post('http://' + self._rhapi.db.option("next_ip") + "/data/heat_data", json=race_data)     
 
        
          

def initialize(rhapi):
    heatSender(rhapi)