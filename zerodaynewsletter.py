import logging
import requests
from lxml import html, etree
import time
import random
from datetime import datetime, timedelta
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import os
import json
import sys

logging.basicConfig(stream=sys.stdout, 
                    level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

SLACK_API_KEY = os.getenv('SLACK_API_KEY')
CHANNEL_ID = os.getenv('CHANNEL_ID')
IMAGE_URL = os.getenv('IMAGE_URL')

def makerequest(url, retries=3, delay=60):
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0"
    headers = {'User-Agent': user_agent}

    for attempt in range(retries):
        try:
            response = requests.get(url, headers=headers)
            logger.info(f"Requesting {url}")

            if response.status_code == 200:
                tree = html.fromstring(response.content)
                return tree
            else:
                logger.info("Failed to make GET request")
                logger.info(response.text)
                logger.info(response.status_code)
                return None

        except requests.exceptions.RequestException as e:
            logger.info(f"Request failed: {e}, attempt {attempt + 1} of {retries}")

            if attempt < retries - 1:
                logger.info(f"Waiting {delay} seconds before retrying...")
                time.sleep(delay)
            else:
                logger.info("Max retries reached. Giving up.")
                return None

        # Optional: random sleep between requests
        time.sleep(random.uniform(1, 5))

def retrieve_advisories():
    tree = makerequest("https://www.zerodayinitiative.com/advisories/published/")
    section = tree.xpath('//section[@class="blueBg up-advisories nopadding"]')
    table = section[0].xpath('.//table[@id="search-table" and contains(@class, "table table-hover table-primary")]')
    tbody = table[0].xpath('.//tbody')
    rows = tbody[0].xpath('.//tr[@id="publishedAdvisories"]')
    advisories = []
    now = datetime.now()
    last_24_hours = now - timedelta(hours=24)

    for row in rows:
        columns = row.xpath('.//td[@class="sort-td"]')
        if columns and len(columns) == 8:
            advisory_date_str = columns[5].text_content().strip()
            advisory_date = datetime.strptime(advisory_date_str, '%Y-%m-%d')
            if advisory_date >= last_24_hours:
                link = columns[7].xpath('.//a/@href')[0] if columns[7].xpath('.//a/@href') else None
                advisory = {
                    "ZDI_ID": columns[0].text_content().strip(),
                    "ZDI_CAN": columns[1].text_content().strip(),
                    "Vendor": columns[2].text_content().strip(),
                    "CVE_ID": columns[3].text_content().strip(),
                    "CVSS": columns[4].text_content().strip(),
                    "Date": columns[5].text_content().strip(),
                    "Extra": columns[6].text_content().strip(),
                    "Description": columns[7].text_content().strip(),
                    "Link": f"https://www.zerodayinitiative.com{link}"
                }
                advisories.append(advisory)

    return advisories

def retrieve_advisory_info(advisory_link):
    logger.info("starting retrieve_advisory_info")
    tree = makerequest(advisory_link)
    section = tree.xpath('//section[@class="blueBg"]')
    content_block = section[0].xpath('.//div[@class="contentBlock advisories-details"]')
    table = content_block[0].xpath('.//table[@style="max-width: 100%;"]')
    tr_elements = table[0].xpath('.//tr')

    product_info = tr_elements[3]
    product_info_td = product_info.xpath('./td')
    affected_product = text = product_info_td[1].text_content().strip()

    vulnerability_info = tr_elements[4]
    vulnerability_info_td = vulnerability_info.xpath('./td')
    vulnerability_details = text = vulnerability_info_td[1].text_content().strip()

    additional_info = tr_elements[5]
    additional_info_td = additional_info.xpath('./td')
    additional_details = text = additional_info_td[1].text_content().strip()
    additional_details_href_element = additional_info_td[1].find('.//a')
    additional_details_hrefs = additional_details_href_element.attrib['href'] if additional_details_href_element is not None else None

    timeline_info = tr_elements[6]
    timeline_info_td = timeline_info.xpath('./td')
    disclosure_timeline = timeline_info_td[1].text_content().strip()

    advisory_information = {"AFFECTED PRODUCTS": affected_product, "VULNERABILITY DETAILS": vulnerability_details, "ADDITIONAL DETAILS": additional_details, "ADDITIONAL DETAILS LINKS": additional_details_hrefs, "DISCLOSURE TIMELINE": disclosure_timeline}


    return advisory_information


def create_slack_message(advisory, advisory_information):
    text_summary = f":red_circle: CVE ID: {advisory['CVE_ID']} - {advisory_information['VULNERABILITY DETAILS'][:75]}..."  # Summary for notifications
    
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*New Zero Day Vulnerability advisory :red_circle:*\n\n"
                    f"*CVE ID:* {advisory['CVE_ID']}\n"
                    f"*CVSS SCORE:* {advisory['CVSS']}\n"
                    f"*AFFECTED VENDOR:* {advisory['Vendor']}\n"
                    f"*AFFECTED PRODUCTS:* {advisory_information['AFFECTED PRODUCTS']}\n"
                    f"*VULNERABILITY DETAILS:* {advisory_information['VULNERABILITY DETAILS']}\n"
                    f"*ADDITIONAL DETAILS:* {advisory_information['ADDITIONAL DETAILS']}\n"
                )
            },
            "accessory": {
                "type": "image",
                "image_url": IMAGE_URL,
                "alt_text": "CVE details"
            }
        },
        {
            "type": "divider"
        }
    ]
    return text_summary, blocks

def create_slack_message_for_no_new_cves():
    text_summary = f":large_green_circle: No new Zero Day advisories in the last 24 hours :large_green_circle:"  # Summary for notifications
    
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":large_green_circle: No new Zero Day advisories in the last 24 hours\n"
                )
            },
            "accessory": {
                "type": "image",
                "image_url": IMAGE_URL,
                "alt_text": "CVE details"
            }
        },
        {
            "type": "divider"
        }
    ]
    return text_summary, blocks

def send_slack_message_for_no_new_zero_day(client):
    text_summary, message_blocks = create_slack_message_for_no_new_cves()
    try:
        response = client.chat_postMessage(
            channel=CHANNEL_ID, 
            text=text_summary,  # Fallback text for notifications
            blocks=json.dumps(message_blocks)
        )
        logger.info("successfully posted CVE into Slack channel")
    except SlackApiError as e:
        logger.error(logger.error(f"Error posting to Slack: {e}"))

def main():
    advisories = retrieve_advisories()
    logger.info(f"The ammount of advisories are {len(advisories)}")
    client = WebClient(token=SLACK_API_KEY)
    time.sleep(60)
    if not advisories:
        send_slack_message_for_no_new_zero_day(client)
    else:
        for advisory in advisories:
            try:
                advisory_link = advisory.get("Link")
                advisory_information = retrieve_advisory_info(advisory_link)
                text_summary, message_blocks = create_slack_message(advisory, advisory_information)
                response = client.chat_postMessage(
                    channel=CHANNEL_ID, 
                    text=text_summary,  # Fallback text for notifications
                    blocks=json.dumps(message_blocks)
                )
                logger.info("successfully posted CVE into Slack channel")
            except Exception as e:
                logger.error(e)
                continue
    

if __name__ == "__main__":
    main()
