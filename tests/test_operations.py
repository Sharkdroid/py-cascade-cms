from cascade_cms.operations import Operations


class FakeDriver:
    def __init__(self):
        self.pending_requests = []
        self.base_url = "https://example.test/api/v1"

    def _build_url(self, *segments):
        return "/".join([self.base_url, *map(str, segments)])


def test_read_queues_a_get_request(page_identifier):
    driver = FakeDriver()
    Operations(driver).read(page_identifier)

    assert len(driver.pending_requests) == 1
    request = driver.pending_requests[0]
    assert request.method == "GET"
    assert request.url == (
        "https://example.test/api/v1/read/page/8b320f55ac1001062545a6d2562cee4b"
    )


def test_read_workflow_information_hits_its_own_endpoint(page_identifier):
    driver = FakeDriver()
    Operations(driver).readWorkflowInformation(page_identifier)

    request = driver.pending_requests[0]
    assert "/readWorkflowInformation/" in request.url
