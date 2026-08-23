@echo off
python -m pip install -r requirements.txt
python -m uvicorn web.app:app --reload
