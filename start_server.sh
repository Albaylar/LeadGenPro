#!/bin/bash
DIR="/Users/furkandenizalbaylar/Desktop/ActiveProjects/LeadGenPro"
cd "$DIR"
source venv/bin/activate
exec uvicorn app:app --host 127.0.0.1 --port 8000 --reload
