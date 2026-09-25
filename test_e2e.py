import sys
import os
import json
import sqlite3
import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock

# Force database to test database or use current database safely
import db
import app
from runner import priority_label

class TestB2BOutreachPro(unittest.TestCase):
    def setUp(self):
        # We will use the live schema or verify directly
        db.init_db()

    def test_priority_label(self):
        self.assertEqual(priority_label(30), "YUKSEK")
        self.assertEqual(priority_label(74), "YUKSEK")
        self.assertEqual(priority_label(75), "ORTA")
        self.assertEqual(priority_label(89), "ORTA")
        self.assertEqual(priority_label(90), "DUSUK")
        self.assertEqual(priority_label(100), "DUSUK")
        self.assertEqual(priority_label(None), "ORTA")

    def test_db_offerings_crud(self):
        print("Testing DB Offering CRUD...")
        # Create
        name = "Test iOS Offering"
        desc = "iOS app development pitch"
        pitch_de = "Hallo, biz iOS app yapıyoruz."
        pitch_en = "Hello, we build iOS apps."
        oid = db.create_offering(name, desc, pitch_de, pitch_en)
        self.assertIsNotNone(oid)
        
        # Read
        offering = db.get_offering(oid)
        self.assertIsNotNone(offering)
        self.assertEqual(offering["name"], name)
        self.assertEqual(offering["description"], desc)
        self.assertEqual(offering["pitch_de"], pitch_de)
        self.assertEqual(offering["pitch_en"], pitch_en)
        
        # List
        offerings = db.get_offerings()
        self.assertTrue(len(offerings) > 0)
        self.assertTrue(any(o["id"] == oid for o in offerings))
        
        # Update
        new_name = "Updated iOS Offering"
        db.update_offering(oid, name=new_name, description=desc, pitch_de=pitch_de, pitch_en=pitch_en)
        offering = db.get_offering(oid)
        self.assertEqual(offering["name"], new_name)
        
        # Delete
        db.delete_offering(oid)
        offering = db.get_offering(oid)
        self.assertIsNone(offering)

    def test_campaign_with_offering(self):
        print("Testing Campaign with Offering...")
        # Create offering
        oid = db.create_offering("Campaign Offering", "Desc", "DE", "EN")
        
        # Create campaign
        cid = db.create_campaign("berlin", ["restaurant"], search_mode="standard", analyze_mode="find_only", offering_id=oid)
        self.assertIsNotNone(cid)
        
        campaign = db.get_campaign(cid)
        self.assertIsNotNone(campaign)
        self.assertEqual(campaign["offering_id"], oid)
        self.assertEqual(campaign["city"], "berlin")
        
        # Clean up offering (campaign stays but offering is deleted or cascade, here SQLite has FK)
        db.delete_offering(oid)

    @patch("imaplib.IMAP4_SSL")
    def test_reply_tracker_agent_no_credentials(self, mock_imap):
        print("Testing Reply Tracker with missing credentials...")
        # Force credentials to empty
        with patch("agents.reply_tracker_agent.get_setting", side_effect=lambda key, default="": ""):
            with self.assertRaises(ValueError):
                from agents.reply_tracker_agent import check_campaign_replies
                check_campaign_replies(1)

    @patch("imaplib.IMAP4_SSL")
    def test_reply_tracker_agent_with_mocked_emails(self, mock_imap_cls):
        print("Testing Reply Tracker with mocked IMAP server...")
        # 1. Setup mock credentials
        def get_setting_mock(key, default=""):
            if key == "gmail_user": return "test@gmail.com"
            if key == "gmail_password": return "app_password"
            return default
            
        # 2. Setup mock campaign and leads
        # Let's create a temporary campaign and a candidate lead
        oid = db.create_offering("Mock Offering", "Desc", "DE", "EN")
        cid = db.create_campaign("berlin", ["restaurant"], offering_id=oid)
        
        # Insert a lead that has email sent but no reply
        with db.conn() as c:
            cur = c.execute(
                "INSERT INTO leads(campaign_id, name, email, email_sent, email_sent_at, reply_received) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (cid, "Target Biz", "prospect@example.com", "Evet", "2026-06-01T22:00:00", 0)
            )
            lid = cur.lastrowid
            
        # 3. Setup Mock IMAP4_SSL Instance
        mock_imap = MagicMock()
        mock_imap_cls.return_value = mock_imap
        
        mock_imap.login.return_value = ("OK", [b"Logged in"])
        mock_imap.select.return_value = ("OK", [b"100"])
        
        # Mock search: returns msg_ids
        mock_imap.search.return_value = ("OK", [b"12345"])
        
        # Mock fetch header: prospect replied on 2026-06-01 23:00:00 (after 22:00:00)
        email_header_content = b"""Date: Mon, 01 Jun 2026 23:00:00 +0000
From: prospect@example.com
Subject: Re: Web Optimization
"""
        mock_imap.fetch.return_value = ("OK", [(b"12345 (RFC822.HEADER)", email_header_content)])
        
        with patch("agents.reply_tracker_agent.get_setting", side_effect=get_setting_mock):
            from agents.reply_tracker_agent import check_campaign_replies
            new_replies = check_campaign_replies(cid)
            
            # Should detect 1 new reply
            self.assertEqual(new_replies, 1)
            
            # Check lead status in DB
            lead = db.get_lead(lid)
            self.assertEqual(lead["reply_received"], 1)
            self.assertIsNotNone(lead["replied_at"])
            print(f"Verified reply marked at: {lead['replied_at']}")

        # Clean up database records
        with db.conn() as c:
            c.execute("DELETE FROM leads WHERE id=?", (lid,))
            c.execute("DELETE FROM campaigns WHERE id=?", (cid,))
        db.delete_offering(oid)

if __name__ == "__main__":
    unittest.main()
