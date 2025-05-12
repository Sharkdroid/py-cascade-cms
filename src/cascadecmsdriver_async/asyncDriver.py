import logging
import aiohttp
import asyncio

class CascadeCMSURLBuilder:
    """
    Builds and collects (method, URL) tuples for Cascade CMS 8 REST API.
    Does not perform HTTP requests—only constructs and collects request data.
    """

    def __init__(self, cascadeUrl):
        self.reqUrls = []
        self.base_url = f"{cascadeUrl}/api/v1"
        self.isFlushed = True

    def _build_url(self, *segments):
        return "/".join([self.base_url, *map(str, segments)])

    def read_asset(self, asset_type='page', asset_identifier=None):
        url = self._build_url('read', asset_type, asset_identifier)
        self.reqUrls.append(('GET', url))

    def read_asset_workflow_settings(self, asset_type='page', asset_identifier=None):
        url = self._build_url('readWorkflowSettings',
                              asset_type, asset_identifier)
        self.reqUrls.append(('GET', url))

    def edit_asset_workflow_settings(self, asset_type='page', asset_identifier=None, payload=None):
        url = self._build_url('editWorkflowSettings',
                              asset_type, asset_identifier)
        self.reqUrls.append(('POST', url))

    def workflows_exist(self, workflow_settings):
        ws = workflow_settings.get('workflowSettings', workflow_settings)
        defs = ws.get('workflowDefinitions', [])
        return bool(defs)

    def get_user_by_email(self, email_address=''):
        self.read_asset('user', email_address)

    def get_group(self, group_name):
        self.read_asset('group', group_name)

    def publish_asset(self, asset_type='page', asset_identifier='', publish_information=None):
        url = self._build_url('publish', asset_type, asset_identifier)
        self.reqUrls.append(('POST', url))

    def unpublish_asset(self, asset_type='page', asset_identifier=''):
        self.publish_asset(asset_type, asset_identifier)

    def copy_asset_to_new_container(self, asset_type='page', asset_identifier='', new_name='', destination_container_identifier=''):
        url = self._build_url('copy', asset_type, asset_identifier)
        self.reqUrls.append(('POST', url))

    def batch(self, operations):
        url = self._build_url('batch')
        self.reqUrls.append(('POST', url))

    def checkIn(self, identifier, comments):
        url = self._build_url(
            'checkIn', identifier.asset_type, identifier.asset_id)
        self.reqUrls.append(('POST', url))

    def checkOut(self, identifier):
        url = self._build_url(
            'checkOut', identifier.asset_type, identifier.asset_id)
        self.reqUrls.append(('POST', url))

    def copy(self, identifier, copyParameters, workflowConfiguration):
        url = self._build_url(
            'copy', identifier.asset_type, identifier.asset_id)
        self.reqUrls.append(('POST', url))

    def create(self, asset):
        url = self._build_url('create')
        self.reqUrls.append(('POST', url))

    def delete(self, identifier, deleteParameters, workflowConfiguration=None):
        url = self._build_url(
            'delete', identifier.asset_type, identifier.asset_id)
        self.reqUrls.append(('POST', url))

    def deleteMessage(self, identifier):
        url = self._build_url(
            'deleteMessage', identifier.asset_type, identifier.asset_id)
        self.reqUrls.append(('POST', url))

    def edit(self, asset):
        url = self._build_url('edit')
        self.reqUrls.append(('POST', url))

    def editAccessRights(self, accessRightsInformation, applyToChildren=False):
        id = accessRightsInformation.identifier
        url = self._build_url('editAccessRights', id.asset_type, id.asset_id)
        self.reqUrls.append(('POST', url))

    def editPreference(self, preference):
        url = self._build_url('editPreference')
        self.reqUrls.append(('POST', url))

    def editWorkflowSettings(self, workflowSettings, applyInheritWorkflowsToChildren=False, applyRequireWorkflowToChildren=False):
        ident = workflowSettings.identifier
        url = self._build_url('editWorkflowSettings',
                              ident.asset_type, ident.asset_id)
        self.reqUrls.append(('POST', url))

    def listEditorConfigurations(self, identifier):
        url = self._build_url('listEditorConfigurations',
                              identifier.asset_type, identifier.asset_id)
        self.reqUrls.append(('GET', url))

    def listMessages(self):
        url = self._build_url('listMessages')
        self.reqUrls.append(('GET', url))

    def listSites(self):
        url = self._build_url('listSites')
        self.reqUrls.append(('GET', url))

    def listSubscribers(self, identifier):
        url = self._build_url(
            'listSubscribers', identifier.asset_type, identifier.asset_id)
        self.reqUrls.append(('GET', url))

    def markMessage(self, identifier, markType):
        url = self._build_url(
            'markMessage', identifier.asset_type, identifier.asset_id)
        self.reqUrls.append(('POST', url))

    def move(self, identifier, moveParameters, workflowConfiguration=None):
        url = self._build_url(
            'move', identifier.asset_type, identifier.asset_id)
        self.reqUrls.append(('POST', url))

    def performWorkflowTransition(self, workflowTransitionInformation):
        url = self._build_url('performWorkflowTransition')
        self.reqUrls.append(('POST', url))

    def publish(self, publishInformation):
        id = publishInformation.identifier
        url = self._build_url('publish', id.asset_type, id.asset_id)
        self.reqUrls.append(('POST', url))

    def read(self, identifier):
        url = self._build_url(
            'read', identifier.asset_type, identifier.asset_id)
        self.reqUrls.append(('GET', url))

    def readAccessRights(self, identifier):
        url = self._build_url('readAccessRights',
                              identifier.asset_type, identifier.asset_id)
        self.reqUrls.append(('GET', url))

    def readAudits(self, auditParameters):
        id = auditParameters.identifier
        url = self._build_url('readAudits', id.asset_type, id.asset_id)
        self.reqUrls.append(('POST', url))

    def readPreferences(self):
        url = self._build_url('readPreferences')
        self.reqUrls.append(('GET', url))

    def readWorkflowInformation(self, identifier):
        url = self._build_url('readWorkflowInformation',
                              identifier.asset_type, identifier.asset_id)
        self.reqUrls.append(('GET', url))

    def readWorkflowSettings(self, identifier):
        url = self._build_url('readWorkflowSettings',
                              identifier.asset_type, identifier.asset_id)
        self.reqUrls.append(('GET', url))

    def search(self, searchInformation):
        url = self._build_url('search')
        self.reqUrls.append(('POST', url))

    def sendMessage(self, message):
        url = self._build_url('sendMessage')
        self.reqUrls.append(('POST', url))

    def siteCopy(self, originalSiteId='', originalSiteName='', newSiteName=''):
        url = self._build_url('siteCopy')
        self.reqUrls.append(('POST', url))



class CascadeCMSRestDriverAsync(CascadeCMSURLBuilder):
    """
    URL builder and executor for Cascade CMS 8 REST API.
    Inherits URL-building from CascadeCMSURLBuilder and applies an optional parser function to each response.
    """

    def __init__(self, cascadeUrl, apiKey, verbose=False, parser_fn=None):
        super().__init__(cascadeUrl)
        self._apiKey = apiKey
        self._parser_fn = parser_fn or (lambda x: x)
        self.setup_logging(verbose)
        self.info("Initializing URL builder")

    def _flush(self):
        """
        Clears the request queue.
        """
        self.reqUrls.clear()
        self.isFlushed = True
        self.info("Flushing request queue")

    async def fetchData(self, session, method, url):
        raw = None
        async with session.request(method, url) as response:
            raw = await response.json()
        # apply parsing callback to raw JSON
        return self._parser_fn(raw)

    async def watcher(self):
        headers = {"Authorization": f"bearer {self._apiKey}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession(headers=headers) as session:
            tasks = [self.fetchData(session, method, url) for method, url in self.reqUrls]
            return await asyncio.gather(*tasks)

    def _submitRequests(self):
        # returns list of parsed responses
        if not(self.isFlushed):
            self.info("Warning: There's a batch of requests present in reqUrls. Did you forget to flush request queue?")
        self.isFlushed = False
        self.info("Submitting current batch requests")
        return asyncio.run(self.watcher())

    def setup_logging(self, verbose=False):
        base_logger = logging.getLogger('CascadeCMSUrlBuilder')
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(prefix)s - %(message)s')
        handler.setFormatter(formatter)
        base_logger.addHandler(handler)
        base_logger.setLevel(logging.DEBUG if verbose else logging.INFO)
        self.prefix = {'prefix': 'CascadeURLBuilder'}
        self.logger = logging.LoggerAdapter(base_logger, self.prefix)

    def info(self, msg):
        self.logger.info(msg, extra=self.prefix)


