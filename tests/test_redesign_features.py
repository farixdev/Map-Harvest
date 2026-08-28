import pytest
import os
import tempfile
import time
from core import outreach_db as DB
from core import settings as ST
from core import campaign as CAMP
from ui import app as APP
from ui import screen_outreach as SO

@pytest.fixture
def temp_env():
    saved = (ST.SETTINGS_DIR, ST.SETTINGS_PATH)
    tmp = tempfile.mkdtemp(prefix="leadforge-test-")
    ST.SETTINGS_DIR = tmp
    ST.SETTINGS_PATH = os.path.join(tmp, "settings.json")
    conn = DB.connect(os.path.join(tmp, "outreach.db"))
    try:
        yield conn, tmp
    finally:
        DB.close_all()
        ST.SETTINGS_DIR, ST.SETTINGS_PATH = saved

def test_campaign_planning_cancellation(temp_env):
    conn, _ = temp_env
    profile = {"sender_name": "Test", "postal_address": "Test Addr"}
    settings = {"smtp_accounts": [{"email": "test@gmail.com", "enabled": True}]}
    
    # Create campaign in preparing state
    campaign_id = DB.create_campaign(conn, "Test Campaign", "gap_direct", profile, settings, status="preparing")
    assert DB.get_campaign(conn, campaign_id)["status"] == "preparing"
    
    # Prepare should_stop to return True immediately (cancelling)
    def should_stop():
        return True
        
    plan = CAMP.plan_campaign(
        conn, campaign_id=campaign_id, leads=[{"email": "l1@example.com"}],
        template_id="gap_direct", profile=profile, settings=settings,
        ai=None, should_stop=should_stop
    )
    
    assert plan["cancelled"] is True
    assert DB.get_campaign(conn, campaign_id)["status"] == "cancelled"

def test_campaign_planning_failure_cleanup(temp_env):
    conn, _ = temp_env
    profile = {"sender_name": "Test", "postal_address": "Test Addr"}
    settings = {"smtp_accounts": [{"email": "test@gmail.com", "enabled": True}]}
    
    campaign_id = DB.create_campaign(conn, "Test Campaign", "gap_direct", profile, settings, status="preparing")
    
    # Intentionally trigger an exception by passing malformed leads list that raises exception in _eligible_leads
    class BadLeads:
        def __iter__(self):
            raise ValueError("Forced error")
            
    plan = CAMP.plan_campaign(
        conn, campaign_id=campaign_id, leads=BadLeads(),
        template_id="gap_direct", profile=profile, settings=settings,
        ai=None
    )
    
    assert "error" in plan
    assert DB.get_campaign(conn, campaign_id)["status"] == "failed"
    
    # Messages should be empty
    messages = DB.rows(conn, "SELECT * FROM messages WHERE campaign_id = ?", (campaign_id,))
    assert len(messages) == 0
