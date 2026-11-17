import json
from pydantic import BaseModel, Field
from .cutting_parameters import CuttingParameters


class MaterialProfile(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    parameters: CuttingParameters


class MaterialProfiles:
    def __init__(self, history):
        self.history = history
        with history.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS profiles(name TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS profile_versions(name TEXT, revision INTEGER, document TEXT NOT NULL, PRIMARY KEY(name,revision))')

    def save(self, profile: MaterialProfile):
        value = profile.model_dump()
        with self.history.connect() as db:
            row = db.execute('SELECT COALESCE(MAX(revision),0) FROM profile_versions WHERE name=?', (profile.name,)).fetchone()
            value['revision'] = row[0]+1
            db.execute('INSERT INTO profile_versions VALUES (?,?,?)', (profile.name, value['revision'], json.dumps(value)))
            db.execute('INSERT INTO profiles VALUES (?,?) ON CONFLICT(name) DO UPDATE SET document=excluded.document',
                       (profile.name, json.dumps(value)))
        return value

    def list(self):
        with self.history.connect() as db:
            rows = db.execute('SELECT document FROM profiles ORDER BY name').fetchall()
        return [json.loads(row[0]) for row in rows]
