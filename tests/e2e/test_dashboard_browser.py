"""Browser regressions against the disposable Docker test stack."""

from __future__ import annotations

import json
import os
import time

import psycopg2
import pytest

selenium = pytest.importorskip("selenium")
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


BASE_URL = os.getenv("TEST_BASE_URL")
DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not BASE_URL or not DATABASE_URL,
    reason="Browser integration tests require the Docker test stack.",
)

TEST_SYSTEM_ID = 9001
TEST_SYSTEM_DECIMAL = 2
TEST_TRIGGER_ID = 9001
TEST_CALL_ID = 900001
TEST_TRIGGER_NAME = "E2E Fire Trigger"


@pytest.fixture(scope="module", autouse=True)
def seed_dashboard_data():
    """Create a system whose display decimal deliberately differs from its ID."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO radio_systems (radio_system_id, system_decimal, system_name)
                VALUES (%s, %s, %s)
                """,
                (TEST_SYSTEM_ID, TEST_SYSTEM_DECIMAL, "E2E Fire System"),
            )
            cur.execute(
                """
                INSERT INTO alert_triggers (
                    alert_trigger_id, radio_system_id, alert_trigger_name,
                    alert_trigger_type, alert_trigger_enabled
                ) VALUES (%s, %s, %s, 'OR', 1)
                """,
                (TEST_TRIGGER_ID, TEST_SYSTEM_ID, TEST_TRIGGER_NAME),
            )
            cur.execute(
                """
                INSERT INTO call_records (
                    call_id, radio_system_id, start_epoch_s, duration_s,
                    talkgroup, talkgroup_name, file_path
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (TEST_CALL_ID, TEST_SYSTEM_ID, int(time.time()), 5.0, 1234, "E2E Fire", "static/audio/e2e.mp3"),
            )
            cur.execute(
                """
                INSERT INTO call_tone_events (
                    call_id, tone_type, tone_set_id, json_payload,
                    freq_a, freq_b, length_a_s, length_b_s, start_s, end_s
                ) VALUES (%s, 'two_tone', 'e2e-1', %s, 600.0, 800.0, 0.5, 0.5, 0.0, 1.0)
                """,
                (TEST_CALL_ID, json.dumps({"tone_a_length": 0.5, "tone_b_length": 0.5})),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--user-data-dir=/tmp/icad-e2e-chromium")
    options.add_argument("--window-size=1440,1100")
    options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    browser = webdriver.Chrome(options=options)
    try:
        yield browser
    finally:
        browser.quit()


def _login(driver):
    driver.get(f"{BASE_URL}/login")
    driver.find_element(By.ID, "loginUsername").send_keys("test-admin")
    driver.find_element(By.ID, "loginPassword").send_keys("TestAdmin!2026")
    driver.find_element(By.ID, "submitLoginBtn").click()
    WebDriverWait(driver, 20).until(lambda browser: "/dashboard" in browser.current_url)


def test_successful_login_has_no_detached_dom_error(driver):
    _login(driver)
    errors = [entry["message"] for entry in driver.get_log("browser") if entry["level"] == "SEVERE"]
    assert not any("Cannot set properties of null" in message for message in errors)


def test_operations_health_loads_from_postgresql(driver):
    _login(driver)
    driver.get(f"{BASE_URL}/dashboard/operations")
    WebDriverWait(driver, 20).until(
        lambda browser: len(browser.find_elements(By.CSS_SELECTOR, "#operationsMetrics .ap-page-stat")) > 0
    )


def test_call_details_lists_existing_triggers_for_its_radio_system(driver):
    _login(driver)
    WebDriverWait(driver, 20).until(
        lambda browser: len(browser.find_elements(By.CSS_SELECTOR, "#callsTable tbody tr")) > 0
    )
    opened = driver.execute_script(
        """
        const row = Array.from(document.querySelectorAll('#callsTable tbody tr'))
            .find(element => element.innerHTML.includes(arguments[0]));
        if (!row) return false;
        row.querySelector('td:nth-child(2)').click();
        return true;
        """,
        str(TEST_CALL_ID),
    )
    assert opened, "Seeded call was not shown in the dashboard"
    WebDriverWait(driver, 15).until(
        lambda browser: browser.find_element(By.ID, "dCallId").text == str(TEST_CALL_ID)
    )
    WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "#dTonesTable .js-create-trigger"))
    ).click()
    WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.ID, "aoaExisting"))).click()
    WebDriverWait(driver, 10).until(
        lambda browser: len(browser.find_elements(By.CSS_SELECTOR, "#aoaExistingSelect option")) > 1
    )
    options = [option.text for option in driver.find_elements(By.CSS_SELECTOR, "#aoaExistingSelect option")]
    assert any(TEST_TRIGGER_NAME in option for option in options)
