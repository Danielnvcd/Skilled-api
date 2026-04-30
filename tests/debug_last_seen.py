# -*- coding: utf-8 -*-
"""Debug: add logging to update_last_seen and run through pytest."""
import os, sys, time, logging
import pytest
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash

# Patch update_last_seen to add debug prints
import app.routes.auth as auth_mod

_original_update_last_seen = auth_mod.update_last_seen

def _debug_update_last_seen():
    from flask import session, request
    from app.extensions import get_redis
    from app.utils import _get_session_user
    from app.extensions import db
    
    print(f"\n[DEBUG update_last_seen] endpoint={request.endpoint}")
    
    if 'user_id' not in session:
        print("[DEBUG] No user_id in session, returning")
        return
    if request.endpoint and 'static' in request.endpoint:
        print("[DEBUG] Static endpoint, returning")
        return
    
    user_id = session['user_id']
    r = get_redis()
    print(f"[DEBUG] user_id={user_id}, redis={r is not None}")
    
    if r:
        cache_key = f"last_seen:{user_id}"
        cached = r.get(cache_key)
        print(f"[DEBUG] Redis cache_key={cache_key}, cached={cached}")
        if cached:
            print("[DEBUG] Cache hit, returning early")
            return
        
        try:
            user = _get_session_user()
            print(f"[DEBUG] user from session: {user}, last_seen={user.last_seen if user else 'N/A'}")
            if user:
                user.last_seen = datetime.now()
                db.session.commit()
                r.setex(cache_key, 300, "1")
                print(f"[DEBUG] Updated last_seen to {user.last_seen}")
        except Exception as e:
            print(f"[DEBUG] EXCEPTION in Redis path: {e}")
            import traceback
            traceback.print_exc()
            db.session.rollback()
    else:
        now = time.time()
        last_update = session.get('_last_seen_ts', 0)
        print(f"[DEBUG] Fallback path: now={now}, last_update={last_update}, diff={now - last_update}")
        if now - last_update < 300:
            print("[DEBUG] Throttled, returning")
            return
        try:
            user = _get_session_user()
            print(f"[DEBUG] user from session: {user}")
            if user:
                user.last_seen = datetime.now()
                db.session.commit()
                session['_last_seen_ts'] = now
                print(f"[DEBUG] Updated last_seen to {user.last_seen}")
        except Exception as e:
            print(f"[DEBUG] EXCEPTION in fallback path: {e}")
            import traceback
            traceback.print_exc()
            db.session.rollback()


if __name__ == '__main__':
    # Run the specific test
    sys.exit(pytest.main([
        'tests/test_last_seen.py::TestUpdateLastSeen::test_primer_request_establece_last_seen',
        '-xvs',
        '-p', 'no:warnings',
    ]))
