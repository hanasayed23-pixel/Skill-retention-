"""Generate private local settings. Never commit instance/ to GitHub."""
from pathlib import Path
import json,secrets
p=Path('instance/local_config.json');p.parent.mkdir(exist_ok=True)
if p.exists():
    print('Existing local configuration preserved. Admin password is in instance/local_config.json')
else:
    data=dict(SECRET_KEY=secrets.token_hex(32),ADMIN_PASSWORD=secrets.token_urlsafe(18),DATABASE_URL='sqlite:///instance/study.db',STUDY_CONTACT='Contact the researcher who invited you.')
    p.write_text(json.dumps(data,indent=2));p.chmod(0o600)
    print('Local settings saved to instance/local_config.json. Open this private file for your admin password.')
print('Run: python app.py   Then open: http://127.0.0.1:5000/admin')
