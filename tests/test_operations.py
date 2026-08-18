from cascade_cms.operations import Operations


class FakeDriver:
    def __init__(self):
        self.base_url = "https://example.test/api/v1"

    def _build_url(self, *segments):
        return "/".join([self.base_url, *map(str, segments)])


def chain_requests(chain):
    """The requests prepared by a chain's first (and here only) node."""
    return chain._head.operation_data["requests"]


def test_read_builds_a_get_request(page_identifier):
    driver = FakeDriver()
    chain = Operations(driver).read(page_identifier)

    requests = chain_requests(chain)
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url == (
        "https://example.test/api/v1/read/page/8b320f55ac1001062545a6d2562cee4b"
    )


def test_read_workflow_information_hits_its_own_endpoint(page_identifier):
    driver = FakeDriver()
    chain = Operations(driver).readWorkflowInformation(page_identifier)

    request = chain_requests(chain)[0]
    assert "/readWorkflowInformation/" in request.url


def test_each_operation_call_starts_its_own_chain(page_identifier):
    driver = FakeDriver()
    operations = Operations(driver)

    first = operations.read(page_identifier)
    second = operations.readWorkflowInformation(page_identifier)

    assert len(operations._chains) == 2
    assert operations._chains[0] is first
    assert operations._chains[1] is second
