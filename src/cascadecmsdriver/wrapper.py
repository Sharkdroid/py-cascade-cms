from driver import CascadeCMSRestDriver


class CascadeWSDL(dict):
    def __init__(self, wsdlResponse):
        super().__init__(wsdlResponse)


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


class CascadeWrapper:
    
    def __init__(self, environmentVariable):
        driver = CascadeCMSRestDriver(api_key=environmentVariable["api_key"], verbose=False)#set to true
        driver.base_url = environmentVariable["cascade_url"]
        self._driver = driver    
    
    def identifierToWSDL(self, identifierList, only=[]):
        return [self.readAndParse(objectType=identiferNode.type, id=identiferNode.id) for identiferNode in identifierList if identiferNode.type in only]

    def convertListSitesToIdentifer(self):
        listSites = self._driver.listSites()
        return [CascadeIdentifier(type=s['type'],id=s['id']) for s in listSites["sites"]]
    
    def parseSearch(self, searchTerm="", searchFields=[], searchTypes=[]):
        payload = { 
            "searchTerms":f"{searchTerm}/*",
            "searchFields": searchFields,
            "searchTypes": searchTypes
        }
        return self._driver.search(payload)["matches"]

    def edit(self):
        return

    def readAndParse(self, objectType, id):
        response = self._driver.read_asset(objectType, id)
        if (response['asset'] is not None):
            response = response['asset'][objectType]
        response['type'] = objectType
        
        #convert possible cascade identifiers into CascadeIdentifer class
        for (propertyName, propertyValue) in response.items():
            if(type(propertyValue) is list and len(propertyValue) > 0):
                response[propertyName] = [CascadeIdentifier(type=child["type"], id=child["id"]) for child in propertyValue if(CascadeIdentifier.jsonToIdentifier(child))]

        return CascadeWSDL(response)