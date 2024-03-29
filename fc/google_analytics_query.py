from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import DateRange
from google.analytics.data_v1beta.types import Dimension
from google.analytics.data_v1beta.types import Metric
from google.analytics.data_v1beta.types import RunReportRequest
import os

GOOGLE_APPLICATION_CREDENTIALS = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')


def get_ga_stats_for_last_week():
    """Runs a simple report on a Google Analytics 4 property."""
    # client to use the credentials specified in GOOGLE_APPLICATION_CREDENTIALS environment variable
    client = BetaAnalyticsDataClient()
    property_id = os.environ.get('GA_FOUR_PROPERTY_ID')

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="firstUserSource"), Dimension(name="pagePath")],
        metrics=[Metric(name="newUsers")],
        date_ranges=[DateRange(start_date="7daysAgo", end_date="yesterday")],
    )
    response = client.run_report(request)
    list_to_return = []
    for row in response.rows:
        page_visited = row.dimension_values[1].value
        list_to_return.append([row.dimension_values[0].value, page_visited, row.metric_values[0].value])
    return list_to_return

if __name__ == '__main__':
    babyvar = get_ga_stats_for_last_week()
