from datetime import datetime, timedelta
import pytest


def _insert_campaign(db_path, offering_id=1):
    import db as db_module
    return db_module.create_campaign("berlin", ["restaurant"], offering_id=offering_id)


def _insert_lead(db_path, cid, email="test@restaurant.de"):
    import sqlite3
    with sqlite3.connect(db_path) as c:
        cur = c.execute(
            "INSERT INTO leads(campaign_id, name, sector, website, email, email_sent) "
            "VALUES(?,?,?,?,?,?)",
            (cid, "Test Restaurant", "restaurant", "https://test.de", email, "Evet"),
        )
        return cur.lastrowid


def _insert_offering(db_path):
    import db as db_module
    return db_module.create_offering("Test Offering", description="Test")


def _insert_outreach(db_path, lead_id, cid, step, sent_at_offset_days=0, status="sent"):
    import sqlite3
    sent_at = (datetime.now() - timedelta(days=sent_at_offset_days)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO outreach_messages(lead_id, campaign_id, sequence_step, sent_at, status, subject, body) "
            "VALUES(?,?,?,?,?,?,?)",
            (lead_id, cid, step, sent_at, status, "Test Subject", "Test Body"),
        )


# ── get_followup_candidates ──────────────────────────────────────────────────

def test_step2_candidate_after_7_days(test_db):
    oid = _insert_offering(test_db)
    cid = _insert_campaign(test_db, offering_id=oid)
    lid = _insert_lead(test_db, cid)
    _insert_outreach(test_db, lid, cid, step=1, sent_at_offset_days=8)

    from followup_runner import get_followup_candidates
    result = get_followup_candidates(cid, step=2, days_wait=7)
    assert len(result) == 1
    assert result[0]["id"] == lid


def test_step2_not_ready_before_7_days(test_db):
    oid = _insert_offering(test_db)
    cid = _insert_campaign(test_db, offering_id=oid)
    lid = _insert_lead(test_db, cid)
    _insert_outreach(test_db, lid, cid, step=1, sent_at_offset_days=3)

    from followup_runner import get_followup_candidates
    result = get_followup_candidates(cid, step=2, days_wait=7)
    assert result == []


def test_excludes_replied_lead(test_db):
    import sqlite3
    oid = _insert_offering(test_db)
    cid = _insert_campaign(test_db, offering_id=oid)
    lid = _insert_lead(test_db, cid)
    _insert_outreach(test_db, lid, cid, step=1, sent_at_offset_days=8)
    with sqlite3.connect(test_db) as c:
        c.execute("UPDATE leads SET reply_received=1 WHERE id=?", (lid,))

    from followup_runner import get_followup_candidates
    result = get_followup_candidates(cid, step=2, days_wait=7)
    assert result == []


def test_excludes_already_sent_step2(test_db):
    oid = _insert_offering(test_db)
    cid = _insert_campaign(test_db, offering_id=oid)
    lid = _insert_lead(test_db, cid)
    _insert_outreach(test_db, lid, cid, step=1, sent_at_offset_days=10)
    _insert_outreach(test_db, lid, cid, step=2, sent_at_offset_days=3)

    from followup_runner import get_followup_candidates
    result = get_followup_candidates(cid, step=2, days_wait=7)
    assert result == []


def test_step3_candidate_after_14_days(test_db):
    oid = _insert_offering(test_db)
    cid = _insert_campaign(test_db, offering_id=oid)
    lid = _insert_lead(test_db, cid)
    _insert_outreach(test_db, lid, cid, step=1, sent_at_offset_days=15)
    _insert_outreach(test_db, lid, cid, step=2, sent_at_offset_days=8)

    from followup_runner import get_followup_candidates
    result = get_followup_candidates(cid, step=3, days_wait=14)
    assert len(result) == 1


# ── count_followup_ready ──────────────────────────────────────────────────────

def test_count_followup_ready(test_db):
    oid = _insert_offering(test_db)
    cid = _insert_campaign(test_db, offering_id=oid)
    lid1 = _insert_lead(test_db, cid, email="a@test.de")
    lid2 = _insert_lead(test_db, cid, email="b@test.de")
    _insert_outreach(test_db, lid1, cid, step=1, sent_at_offset_days=8)   # step2 hazır
    _insert_outreach(test_db, lid2, cid, step=1, sent_at_offset_days=15)  # step2 hazır
    _insert_outreach(test_db, lid2, cid, step=2, sent_at_offset_days=8)   # step3 hazır

    from followup_runner import count_followup_ready
    assert count_followup_ready(cid) == 2  # lid1:step2 + lid2:step3 (lid2 step2 already sent)
