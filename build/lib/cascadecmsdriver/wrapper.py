from .driver import CascadeCMSRestDriver
from .cmstypes import CascadeWSDL, CascadeIdentifier, SearchInformation


class CascadeWrapper:
    
    def __init__(self, environmentVariable):
        driver = CascadeCMSRestDriver(api_key=environmentVariable["api_key"], verbose=False)#set to true
        driver.base_url = environmentVariable["cascade_url"]
        self._driver = driver    
    
    def identifierToWSDL(self, identifierList, only=[]):
        return [self.readAndParse(objectType=identiferNode.type, id=identiferNode.id) for identiferNode in identifierList if identiferNode.type in only]

    def convertListSitesToIdentifier(self):
        listSites = self._driver.listSites()
        return [CascadeIdentifier(type=s['type'],id=s['id']) for s in listSites["sites"]]
    
    def parseSearch(self, searchTerm="", searchFields=[], searchTypes=[], includeFileExtensions=()):
        
        payload = SearchInformation(searchTerm, searchFields, searchTypes)

        matches = self._driver.search(payload)["matches"]
        if(len(matches) == 0):
            return []
        matches = CascadeIdentifier.jsonToIdentifier(matches)
        matches = self.identifierToWSDL(matches)

        if(len(includeFileExtensions) == 0):
            return matches
        # filters results based on the path of the file. Ex. If I only want image file types then the results should only be .png, .jpg
        filtered = [match for match in matches if match['name'].endswith(includeFileExtensions)]
        return filtered 

    def edit(self, asset):
        status = self._driver.edit(asset)
        return status

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