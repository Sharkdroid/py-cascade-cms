#import asyncio
#from typing import List, Dict, Any, Optional

from .asyncDriver import CascadeCMSRestDriverAsync
#import os
#from dotenv import load_dotenv



class CascadeWSDL(dict):
    def __init__(self, wsdlResponse):
        super().__init__(wsdlResponse)
    def __repr__(self):
        # Custom representation for when the object is part of a list
        return f"<CascadeWSDL object at {hex(id(self))}>"
    def __str__(self):
        # Actual content when the object is accessed and printed
        return super().__repr__()


class CascadeIdentifier:

    @staticmethod
    def jsonToIdentifier(jsonObject):
        if(type(jsonObject) is not dict):
            return False
        respFormat = {
            "id":str(),
            "type":str(),
            "path":dict(),
            "recycled":bool()
        }
        
        if(respFormat.keys() != jsonObject.keys()):
            return False
        
        for key in jsonObject.keys():
            if type(jsonObject[key]) != type(respFormat[key]):
                return False
        
        return True

    def __init__(self, type, id):
        self.type = type
        self.id = id

class CascadeWrapperAsync:
    @staticmethod
    def _requestParser(response):    
        if ('asset' in response):
            objectType = next(iter(response['asset'].keys()))
            response = response['asset'][objectType]
            response['type'] = objectType

            # convert possible cascade identifiers into CascadeIdentifer class
            for (propertyName, propertyValue) in response.items():
                if (type(propertyValue) is list and len(propertyValue) > 0):
                    response[propertyName] = [CascadeIdentifier(type=child["type"], id=child["id"]) for child in propertyValue if (CascadeIdentifier.jsonToIdentifier(child))]
        
        return CascadeWSDL(response)

    def __init__(self, environmentVariable):
        driver = CascadeCMSRestDriverAsync(apiKey=environmentVariable["api_key"], cascadeUrl=environmentVariable["cascade_url"], parser_fn=CascadeWrapperAsync._requestParser, verbose=False)#set to true
        self._driver = driver    
    
    def identifierToWSDL(self, identifierList, only=[]):
        return [self.readAndParse(objectType=identiferNode.type, id=identiferNode.id) for identiferNode in identifierList if identiferNode.type in only]

    def convertListSitesToIdentifier(self):
        self._driver.listSites()
        listSites = self._driver._submitRequests()[0]
        self._driver._flush()
        return [CascadeIdentifier(type=site['type'],id=site['id']) for site in listSites["sites"]]
    
    def parseSearch(self, searchTerm="", searchFields=[], searchTypes=[]):
        payload = { 
            "searchTerms":f"{searchTerm}/*",
            "searchFields": searchFields,
            "searchTypes": searchTypes
        }
        return self._driver.search(payload)["matches"]

    def edit(self):
        return

    def readAndParse(self, typesAndIds):
        #expecting value like [cascadeIdentifier('folder', 'wehff32890fedfe8fsdc'),...]
        [self._driver.read_asset(identifier.type, identifier.id) for identifier in typesAndIds]
        #submit list of objects
        responses = self._driver._submitRequests()
        self._driver._flush()
        return responses
        
        
"""
def main():

    VARIABLES = {}
    load_dotenv()
    VARIABLES["api_key"] = os.getenv("API_KEY")
    VARIABLES["cascade_url"] = os.getenv("CASCADE_URL")

    cascade = CascadeWrapperAsync(VARIABLES)
    listedSites = cascade.convertListSitesToIdentifer()
    lists = cascade.readAndParse(listedSites)


main()

"""



