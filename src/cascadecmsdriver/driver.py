""" Driver module for interacting with Cascade CMS 8 REST API provided by Hannon Hill
for enterprise-scale content management. """

import requests
import logging
import json
from cmstypes import *
from zeep import Client, xsd
from zeep.transports import Transport
import requests_cache


#TODO: update all Identifier types to use CascadeIdentifier class instead.

class CascadeCMSRestDriver:
    CACHE_LOCATION="./app/cache" #with .sqlite at the end
    def __init__(self, organization_name="", username="", password="", api_key="", verbose=False):
        self.setup_logging(verbose=verbose)
        self.info('Setting up new driver')
        self.organization_name = organization_name
        self.base_url = f'https://{self.organization_name}.cascadecms.com'
        self.session = requests_cache.CachedSession(cache_name=CascadeCMSRestDriver.CACHE_LOCATION,expire_after=259200)
        if username == "" and password == "":
            assert api_key != ""
            self.debug(f"Using API Key: {api_key}")
            self.session.headers = {
                'Authorization': f'Bearer {api_key}'
            }
        if api_key == "":
            assert username != "" and password != ""
            self.debug(f'Using username/password authentication')
            self.session.auth = requests.auth.HTTPBasicAuth(username, password)

    def setup_logging(self, verbose=False):
        self.logger = logging.getLogger('Cascade CMS Driver')
        formatter = logging.Formatter('%(prefix)s - %(message)s')
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        self.prefix = {'prefix': 'Cascade REST Driver'}
        self.logger.addHandler(handler)
        self.logger = logging.LoggerAdapter(self.logger, self.prefix)
        if verbose:
            self.logger.setLevel(logging.DEBUG)
            self.logger.debug('Debug mode enabled', extra=self.prefix)
        else:
            self.logger.setLevel(logging.INFO)

    def debug(self, msg):
        self.logger.debug(msg, extra=self.prefix)

    def info(self, msg):
        self.logger.info(msg, extra=self.prefix)

    def error(self, msg):
        self.logger.error(msg, extra=self.prefix)

    def read_asset(self, asset_type='page', asset_identifier=None):
        url = f'{self.base_url}/api/v1/read/{asset_type}/{asset_identifier}'
        self.debug(f'Reading {asset_type} {asset_identifier} at {url}')
        return self.session.get(url).json()

    def read_asset_workflow_settings(self, asset_type='page', asset_identifier=None):
        url = f'{self.base_url}/api/v1/readWorkflowSettings/{asset_type}/{asset_identifier}'
        self.debug(f'Reading workflow settings for {asset_type} {asset_identifier} at {url}')
        return self.session.get(url).json()

    def edit_asset_workflow_settings(self, asset_type='page', asset_identifier=None, payload=None):
        if payload and isinstance(payload, dict) and 'workflowSettings' in payload:
            url = f'{self.base_url}/api/v1/editWorkflowSettings/{asset_type}/{asset_identifier}'
            self.debug(f'Editing workflow settings for {asset_type} {asset_identifier} at {url}')
            body = json.dumps(payload)
            return self.session.post(url, data=body).json()
        else:
            self.error('Payload must include workflowSettings dict')
            return None

    def workflows_exist(self, workflow_settings):
        ws = workflow_settings.get('workflowSettings', workflow_settings)
        defs = ws.get('workflowDefinitions', [])
        return bool(defs)

    def get_user_by_email(self, email_address=""):
        return self.read_asset(asset_type='user', asset_identifier=email_address)

    def get_group(self, group_name):
        return self.read_asset(asset_type='group', asset_identifier=group_name)

    def publish_asset(self, asset_type='page', asset_identifier='', publish_information=None):
        url = f'{self.base_url}/api/v1/publish/{asset_type}/{asset_identifier}'
        self.debug(f'Publishing {asset_type} {asset_identifier} at {url}')
        body = json.dumps(publish_information) if publish_information else None
        return self.session.post(url, data=body).json()

    def unpublish_asset(self, asset_type='page', asset_identifier=''):
        self.debug(f'Unpublishing {asset_type} {asset_identifier}')
        return self.publish_asset(asset_type, asset_identifier, {'unpublish': True})

    def copy_asset_to_new_container(self, asset_type='page', asset_identifier='', new_name='', destination_container_identifier=''):
        url = f'{self.base_url}/api/v1/copy/{asset_type}/{asset_identifier}'
        payload = {
            'copyParameters': {
                'destinationContainerIdentifier': {
                    'type': 'folder',
                    'id': destination_container_identifier
                },
                'doWorkflow': False,
                'newName': new_name
            }
        }
        self.debug(f'Copying asset payload: {payload} to {url}')
        return self.session.post(url, data=json.dumps(payload)).json()

    def batch(self, operations: [Operation]):
        url = f'{self.base_url}/api/v1/batch'
        ops_list = [json.loads(op.toJson()) for op in operations]
        payload = {'operations': ops_list}
        self.debug(f'Batch payload: {payload}')
        return self.session.post(url, data=json.dumps(payload)).json()

    def checkIn(self, identifier: Identifier, comments: str):
        url = f'{self.base_url}/api/v1/checkIn/{identifier.asset_type}/{identifier.asset_id}'
        payload = CheckIn(identifier=identifier, comments=comments).toJson()
        self.debug(f'CheckIn payload: {payload} to {url}')
        return self.session.post(url, data=payload).json()

    def checkOut(self, identifier: Identifier):
        url = f'{self.base_url}/api/v1/checkOut/{identifier.asset_type}/{identifier.asset_id}'
        payload = CheckOut(identifier=identifier).toJson()
        self.debug(f'CheckOut payload: {payload} to {url}')
        return self.session.post(url, data=payload).json()

    def copy(self, identifier: Identifier, copyParameters: CopyParameters, workflowConfiguration: WorkflowConfiguration):
        url = f'{self.base_url}/api/v1/copy/{identifier.asset_type}/{identifier.asset_id}'
        payload = Copy(identifier=identifier, copyParameters=copyParameters, workflowConfiguration=workflowConfiguration).toJson()
        self.debug(f'Copy payload: {payload} to {url}')
        return self.session.post(url, data=payload).json()

    def create(self, asset: Asset):
        url = f'{self.base_url}/api/v1/create'
        body = json.loads(asset.toJson())
        payload = {'asset': body.get('asset', body)}
        self.debug(f'Create payload: {payload}')
        return self.session.post(url, data=json.dumps(payload)).json()

    def delete(self, identifier: Identifier, deleteParameters: DeleteParameters, workflowConfiguration: WorkflowConfiguration=None):
        url = f'{self.base_url}/api/v1/delete/{identifier.asset_type}/{identifier.asset_id}'
        payload = {'deleteParameters': json.loads(deleteParameters.toJson())}
        if workflowConfiguration:
            payload['workflowConfiguration'] = json.loads(workflowConfiguration.toJson())
        self.debug(f'Delete payload: {payload}')
        return self.session.post(url, data=json.dumps(payload)).json()

    def deleteMessage(self, identifier: Identifier):
        url = f'{self.base_url}/api/v1/deleteMessage/{identifier.asset_type}/{identifier.asset_id}'
        self.debug(f'DeleteMessage at {url}')
        return self.session.post(url).json()

    def edit(self, asset: Asset):
        url = f'{self.base_url}/api/v1/edit'
        payload = asset
        self.debug(f'Edit payload: {payload}')
        return self.session.post(url, data=json.dumps(payload)).json()

    def editAccessRights(self, accessRightsInformation: AccessRightsInformation, applyToChildren: bool=False):
        url = f'{self.base_url}/api/v1/editAccessRights/{accessRightsInformation.identifier.asset_type}/{accessRightsInformation.identifier.asset_id}'
        payload = {'accessRightsInformation': json.loads(accessRightsInformation.toJson()), 'applyToChildren': applyToChildren}
        self.debug(f'EditAccessRights payload: {payload}')
        return self.session.post(url, data=json.dumps(payload)).json()

    def editPreference(self, preference: Preference):
        url = f'{self.base_url}/api/v1/editPreference'
        payload = {'preference': json.loads(preference.toJson())}
        self.debug(f'EditPreference payload: {payload}')
        return self.session.post(url, data=json.dumps(payload)).json()

    def editWorkflowSettings(self, workflowSettings: WorkflowSettings, applyInheritWorkflowsToChildren: bool=False, applyRequireWorkflowToChildren: bool=False):
        ident = workflowSettings.identifier
        url = f'{self.base_url}/api/v1/editWorkflowSettings/{ident.asset_type}/{ident.asset_id}'
        payload = {'workflowSettings': json.loads(workflowSettings.toJson()),
                   'applyInheritWorkflowsToChildren': applyInheritWorkflowsToChildren,
                   'applyRequireWorkflowToChildren': applyRequireWorkflowToChildren}
        self.debug(f'EditWorkflowSettings payload: {payload}')
        return self.session.post(url, data=json.dumps(payload)).json()

    def listEditorConfigurations(self, identifier: Identifier):
        url = f'{self.base_url}/api/v1/listEditorConfigurations/{identifier.asset_type}/{identifier.asset_id}'
        self.debug(f'Listing EditorConfigurations at {url}')
        return self.session.get(url).json()

    def listMessages(self):
        url = f'{self.base_url}/api/v1/listMessages'
        self.debug(f'Listing Messages at {url}')
        return self.session.get(url).json()

    def listSites(self):
        url = f'{self.base_url}/api/v1/listSites'
        self.debug(f'Listing Sites at {url}')
        return self.session.get(url).json()

    def listSubscribers(self, identifier: Identifier):
        url = f'{self.base_url}/api/v1/listSubscribers/{identifier.asset_type}/{identifier.asset_id}'
        self.debug(f'Listing Subscribers at {url}')
        return self.session.get(url).json()

    def markMessage(self, identifier: Identifier, markType: MessageMarkType):
        url = f'{self.base_url}/api/v1/markMessage/{identifier.asset_type}/{identifier.asset_id}'
        payload = {'markType': markType.value if hasattr(markType, 'value') else str(markType)}
        self.debug(f'MarkMessage payload: {payload}')
        return self.session.post(url, data=json.dumps(payload)).json()

    def move(self, identifier: Identifier, moveParameters: MoveParameters, workflowConfiguration: WorkflowConfiguration=None):
        url = f'{self.base_url}/api/v1/move/{identifier.asset_type}/{identifier.asset_id}'
        payload = {'moveParameters': json.loads(moveParameters.toJson())}
        if workflowConfiguration:
            payload['workflowConfiguration'] = json.loads(workflowConfiguration.toJson())
        self.debug(f'Move payload: {payload}')
        return self.session.post(url, data=json.dumps(payload)).json()

    def performWorkflowTransition(self, workflowTransitionInformation: WorkflowTransitionInformation):
        url = f'{self.base_url}/api/v1/performWorkflowTransition'
        payload = {'workflowTransitionInformation': json.loads(workflowTransitionInformation.toJson())}
        self.debug(f'PerformWorkflowTransition payload: {payload}')
        return self.session.post(url, data=json.dumps(payload)).json()

    def publish(self, publishInformation: PublishInformation):
        ident = publishInformation.identifier
        url = f'{self.base_url}/api/v1/publish/{ident.asset_type}/{ident.asset_id}'
        payload = {'publishInformation': json.loads(publishInformation.toJson())}
        self.debug(f'Publish payload: {payload}')
        return self.session.post(url, data=json.dumps(payload)).json()

    def read(self, identifier: Identifier):
        url = f'{self.base_url}/api/v1/read/{identifier.asset_type}/{identifier.asset_id}'
        self.debug(f'Reading asset at {url}')
        return self.session.get(url).json()

    def readAccessRights(self, identifier: Identifier):
        url = f'{self.base_url}/api/v1/readAccessRights/{identifier.asset_type}/{identifier.asset_id}'
        self.debug(f'Reading access rights at {url}')
        return self.session.get(url).json()

    def readAudits(self, auditParameters: AuditParameters):
        url = f'{self.base_url}/api/v1/readAudits/{auditParameters.identifier.asset_type}/{auditParameters.identifier.asset_id}'
        payload = {'auditParameters': json.loads(auditParameters.toJson())}
        self.debug(f'ReadAudits payload: {payload}')
        return self.session.post(url, data=json.dumps(payload)).json()

    def readPreferences(self):
        url = f'{self.base_url}/api/v1/readPreferences'
        self.debug(f'Reading preferences at {url}')
        return self.session.get(url).json()

    def readWorkflowInformation(self, identifier: Identifier):
        url = f'{self.base_url}/api/v1/readWorkflowInformation/{identifier.asset_type}/{identifier.asset_id}'
        self.debug(f'ReadWorkflowInformation at {url}')
        return self.session.get(url).json()

    def readWorkflowSettings(self, identifier: Identifier):
        url = f'{self.base_url}/api/v1/readWorkflowSettings/{identifier.asset_type}/{identifier.asset_id}'
        self.debug(f'ReadWorkflowSettings at {url}')
        return self.session.get(url).json()

    def search(self, searchInformation: SearchInformation):
        url = f'{self.base_url}/api/v1/search'
        payload = {'searchInformation': json.loads(searchInformation.toJson())}
        self.debug(f'Search payload: {payload}')
        return self.session.post(url, data=json.dumps(payload)).json()

    def sendMessage(self, message: Message):
        url = f'{self.base_url}/api/v1/sendMessage'
        payload = {'message': json.loads(message.toJson())}
        self.debug(f'SendMessage payload: {payload}')
        return self.session.post(url, data=json.dumps(payload)).json()

    def siteCopy(self, originalSiteId: str = '', originalSiteName: str = '', newSiteName: str = ''):
        url = f'{self.base_url}/api/v1/siteCopy'
        data = {'newSiteName': newSiteName}
        if originalSiteId.strip():
            data['originalSiteId'] = originalSiteId
        elif originalSiteName:
            data['originalSiteName'] = originalSiteName
        self.debug(f'SiteCopy payload: {data}')
        return self.session.post(url, data=json.dumps(data)).json()


