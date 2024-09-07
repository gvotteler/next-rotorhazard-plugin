import logging
from eventmanager import Evt
from Database import Pilot, Profiles
import json
import RHAPI
import requests
from RHUI import UIField, UIFieldType, UIFieldSelectOption


logger = logging.getLogger(__name__)

class heatSender: 

    def __init__(self, rhapi:RHAPI.RHAPI):
        # Store the RHAPI object to allow connections in the future 
        self._rhapi = rhapi

        # Setup an event for when the active heat changes
        # Avaliable events can be found at https://github.com/RotorHazard/RotorHazard/blob/264d39a01c2a391c4ed059d84c5f21969f14dae9/src/server/eventmanager.py#L100
        self._rhapi.events.on(Evt.HEAT_SET, self.onHeatChange)
        rhapi.ui.register_panel("next_format", "Next", "format")
      
        rhapi.fields.register_option( UIField('next_ip', "IP Next Server", UIFieldType.TEXT), 'next_format' )

    def onHeatChange(self, args):


        # The HEAT_SET event passes a dict as an arg. This dict contains the heat_id you will be switching to
        # https://github.com/RotorHazard/RotorHazard/blob/264d39a01c2a391c4ed059d84c5f21969f14dae9/src/server/RHRace.py#L1758
        heat_id = args['heat_id']

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
                        "frequency":slots_frequencies[slot_index]
                }
                
                payload.append(data_dic)
                logger.info(data_dic)
                
        logger.info("Next push data:" + self._rhapi.db.option("next_ip") + "/v1/next/data/pilots")
        response = requests.post(self._rhapi.db.option("next_ip") + ":5000/data/pilots", json=payload)

def initialize(rhapi):
    heatSender(rhapi)
