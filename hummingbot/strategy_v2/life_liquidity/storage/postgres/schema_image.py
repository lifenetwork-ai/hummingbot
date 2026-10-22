"""Offline catalog and monotonic sequence evidence, never a permission cache.

Definitions use pg_catalog's deparsers, effective ACLs and qualified identities;
local OIDs and the migration owner's spelling are not portable identities.
"""
import hashlib
import json
from dataclasses import asdict, dataclass

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, digest, fingerprint

NAMESPACES = ('life_migrations', 'life_storage', 'life_financial', 'life_recorder')


def _acl(expression, kind, owner):
    return f"""(SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'grantor',CASE WHEN x.grantor={owner} THEN '@OWNER' ELSE pg_get_userbyid(x.grantor) END,
        'grantee',CASE WHEN x.grantee=0 THEN '@PUBLIC' WHEN x.grantee={owner} THEN '@OWNER' ELSE '@ROLE:'||pg_get_userbyid(x.grantee) END,
        'privilege',x.privilege_type,'grantable',x.is_grantable) ORDER BY CASE WHEN x.grantor={owner} THEN '@OWNER' ELSE pg_get_userbyid(x.grantor) END,
        CASE WHEN x.grantee=0 THEN '@PUBLIC' WHEN x.grantee={owner} THEN '@OWNER' ELSE '@ROLE:'||pg_get_userbyid(x.grantee) END,
        x.privilege_type,x.is_grantable),'[]'::jsonb)
        FROM aclexplode(COALESCE({expression},acldefault('{kind}',{owner}))) x)"""


ACL_NS = _acl('n.nspacl', 'n', 'n.nspowner')
ACL_REL = "(CASE WHEN c.relkind='S' THEN " + _acl('c.relacl', 's', 'c.relowner') + " ELSE " + _acl('c.relacl', 'r', 'c.relowner') + ' END)'
ACL_FUN = _acl('p.proacl', 'f', 'p.proowner')
ACL_TYPE = _acl('t.typacl', 'T', 't.typowner')
ACL_DEFAULT = _acl('d.defaclacl', 'r', 'd.defaclrole')
ACL_COL = _acl('a.attacl', 'c', 'c.relowner')

CATALOG = {
    'database': """SELECT '@DATABASE'::text AS name,jsonb_build_object('encoding',pg_encoding_to_char(encoding),
        'provider',datlocprovider,'collation',datcollate,'ctype',datctype,'icu_locale',daticulocale,
        'collation_version',datcollversion,'actual_collation_version',pg_database_collation_actual_version(oid)) AS body
        FROM pg_database WHERE datname=current_database() AND cardinality($1::text[])>0""",
    'namespace': f"""SELECT n.nspname AS name,jsonb_build_object('acl',{ACL_NS},
        'owner',CASE WHEN n.nspowner=current_user::regrole THEN '@OWNER' ELSE pg_get_userbyid(n.nspowner) END,
        'comment',obj_description(n.oid,'pg_namespace')) AS body
        FROM pg_namespace n WHERE n.nspname=ANY($1::text[]) OR n.nspname='public'""",
    'relation': f"""SELECT n.nspname||'.'||c.relname AS name,jsonb_build_object(
        'replica_identity',c.relreplident,'kind',c.relkind,'persistence',c.relpersistence,'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity,
        'options',c.reloptions,'tablespace',COALESCE(t.spcname,'@DEFAULT'),'acl',{ACL_REL},
        'partition',pg_get_expr(c.relpartbound,c.oid),'partition_key',pg_get_partkeydef(c.oid),
        'view',CASE WHEN c.relkind='v' THEN pg_get_viewdef(c.oid,false) ELSE NULL END,
        'comment',obj_description(c.oid,'pg_class')) AS body
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_tablespace t ON t.oid=c.reltablespace
        WHERE n.nspname=ANY($1::text[]) AND c.relkind IN ('r','p','S','v')""",
    'column': f"""SELECT n.nspname||'.'||c.relname||'.'||a.attnum::text AS name,jsonb_build_object(
        'name',a.attname,'type',format_type(a.atttypid,a.atttypmod),'notnull',a.attnotnull,
        'identity',a.attidentity,'generated',a.attgenerated,'default',pg_get_expr(d.adbin,d.adrelid),
        'collation',CASE WHEN a.attcollation<>0 THEN a.attcollation::regcollation::text ELSE NULL END,
        'storage',a.attstorage,'compression',a.attcompression,'options',a.attoptions,'acl',{ACL_COL},
        'comment',col_description(c.oid,a.attnum)) AS body
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        WHERE n.nspname=ANY($1::text[]) AND c.relkind IN ('r','p','S','v') AND a.attnum>0 AND NOT a.attisdropped""",
    'constraint': """SELECT n.nspname||'.'||COALESCE(c.relname,t.typname)||'.'||x.conname AS name,
        jsonb_build_object('definition',pg_get_constraintdef(x.oid,false),'validated',x.convalidated,
        'deferrable',x.condeferrable,'deferred',x.condeferred,'comment',obj_description(x.oid,'pg_constraint')) AS body
        FROM pg_constraint x JOIN pg_namespace n ON n.oid=x.connamespace
        LEFT JOIN pg_class c ON c.oid=x.conrelid LEFT JOIN pg_type t ON t.oid=x.contypid
        WHERE n.nspname=ANY($1::text[])""",
    'index': """SELECT n.nspname||'.'||c.relname AS name,jsonb_build_object('definition',pg_get_indexdef(i.indexrelid),
        'valid',i.indisvalid,'ready',i.indisready,'clustered',i.indisclustered,'replident',i.indisreplident,
        'options',c.reloptions,'tablespace',COALESCE(t.spcname,'@DEFAULT')) AS body
        FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_tablespace t ON t.oid=c.reltablespace WHERE n.nspname=ANY($1::text[])""",
    'function': f"""SELECT p.oid::regprocedure::text AS name,jsonb_build_object('definition',pg_get_functiondef(p.oid),
        'acl',{ACL_FUN},'comment',obj_description(p.oid,'pg_proc')) AS body
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=ANY($1::text[])""",
    'trigger': """SELECT n.nspname||'.'||c.relname||'.'||t.tgname AS name,
        jsonb_build_object('definition',pg_get_triggerdef(t.oid,false),'enabled',t.tgenabled) AS body
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=ANY($1::text[]) AND NOT t.tgisinternal""",
    'internal_trigger': """SELECT n.nspname||'.'||c.relname||'.'||x.conname||'.'||t.tgtype::text||'.'||t.tgfoid::regprocedure::text AS name,
        jsonb_build_object('enabled',t.tgenabled,'function',t.tgfoid::regprocedure::text,
            'deferrable',t.tgdeferrable,'deferred',t.tginitdeferred,'constraint',pg_get_constraintdef(x.oid,false),
            'related_relation',t.tgconstrrelid::regclass::text,'attributes',t.tgattr::text,'arguments',t.tgargs::text) AS body
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_constraint x ON x.oid=t.tgconstraint WHERE n.nspname=ANY($1::text[]) AND t.tgisinternal""",
    'policy': """SELECT n.nspname||'.'||c.relname||'.'||p.polname AS name,jsonb_build_object('command',p.polcmd,
        'permissive',p.polpermissive,'using',pg_get_expr(p.polqual,p.polrelid),'check',pg_get_expr(p.polwithcheck,p.polrelid),
        'roles',(SELECT jsonb_agg(CASE WHEN r=0 THEN '@PUBLIC' ELSE '@ROLE:'||pg_get_userbyid(r) END ORDER BY
            CASE WHEN r=0 THEN '@PUBLIC' ELSE '@ROLE:'||pg_get_userbyid(r) END) FROM unnest(p.polroles) r)) AS body
        FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY($1::text[])""",
    'default_acl': f"""SELECT COALESCE(n.nspname,'@GLOBAL')||'.'||d.defaclobjtype::text AS name,
        jsonb_build_object('acl',{ACL_DEFAULT}) AS body FROM pg_default_acl d
        LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace
        WHERE d.defaclrole=current_user::regrole AND (d.defaclnamespace=0 OR n.nspname=ANY($1::text[]))""",
    'type': f"""SELECT n.nspname||'.'||t.typname AS name,jsonb_build_object('kind',t.typtype,
        'acl',{ACL_TYPE},'base',format_type(t.typbasetype,t.typtypmod),'notnull',t.typnotnull,'default',t.typdefault,
        'collation',CASE WHEN t.typcollation<>0 THEN t.typcollation::regcollation::text ELSE NULL END,
        'enum',(SELECT jsonb_agg(enumlabel ORDER BY enumsortorder) FROM pg_enum e WHERE e.enumtypid=t.oid)) AS body
        FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname=ANY($1::text[]) AND t.typtype IN ('d','e')""",
    'sequence': """SELECT n.nspname||'.'||c.relname AS name,(to_jsonb(s)-'seqrelid')||jsonb_build_object('owned_by',
            (SELECT jsonb_agg(jsonb_build_object('table',tn.nspname||'.'||r.relname,'column',a.attname,'dependency',d.deptype)
                ORDER BY tn.nspname,r.relname,a.attname) FROM pg_depend d JOIN pg_class r ON r.oid=d.refobjid
                JOIN pg_namespace tn ON tn.oid=r.relnamespace JOIN pg_attribute a ON a.attrelid=r.oid AND a.attnum=d.refobjsubid
                WHERE d.classid='pg_class'::regclass AND d.refclassid='pg_class'::regclass AND d.objid=c.oid AND d.deptype IN ('a','i'))) AS body
        FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=ANY($1::text[])""",
    'rule': """SELECT n.nspname||'.'||c.relname||'.'||r.rulename AS name,
        jsonb_build_object('definition',pg_get_ruledef(r.oid,false),'enabled',r.ev_enabled) AS body
        FROM pg_rewrite r JOIN pg_class c ON c.oid=r.ev_class JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=ANY($1::text[])""",
}


@dataclass(frozen=True)
class CatalogClass:
    kind: str
    objects: int
    bytes: int
    sha256: str

    def __post_init__(self):
        digest(self.sha256)
        if self.kind not in CATALOG or any(type(v) is not int or v < 0 for v in (self.objects, self.bytes)):
            raise StorageError('PG_SCHEMA_IMAGE_INVALID')


@dataclass(frozen=True)
class SequenceState:
    name: str
    last_value: int
    is_called: bool

    def __post_init__(self):
        if type(self.name) is not str or not 0 < len(self.name) <= 512 or type(self.last_value) is not int or type(self.is_called) is not bool:
            raise StorageError('PG_SEQUENCE_IMAGE_INVALID')


@dataclass(frozen=True)
class SchemaImage:
    classes: tuple[CatalogClass, ...]
    sequences: tuple[SequenceState, ...]

    def __post_init__(self):
        if (type(self.classes) is not tuple or any(type(c) is not CatalogClass for c in self.classes)
                or tuple(c.kind for c in self.classes) != tuple(sorted(CATALOG)) or type(self.sequences) is not tuple
                or any(type(s) is not SequenceState for s in self.sequences)
                or tuple(s.name for s in self.sequences) != tuple(sorted(set(s.name for s in self.sequences)))):
            raise StorageError('PG_SCHEMA_IMAGE_INVALID')

    def json(self):
        return dict(format='LIFE_PG_SCHEMA_V1', classes=[asdict(c) for c in self.classes], sequences=[asdict(s) for s in self.sequences])

    @property
    def content_digest(self):
        return fingerprint(self.json())

    @classmethod
    def from_json(cls, value):
        try:
            if type(value) is not dict or set(value) != {'format', 'classes', 'sequences'} or value['format'] != 'LIFE_PG_SCHEMA_V1' or type(value['classes']) is not list or type(value['sequences']) is not list:
                raise ValueError()
            return cls(tuple(CatalogClass(**v) for v in value['classes']), tuple(SequenceState(**v) for v in value['sequences']))
        except (ValueError, TypeError, KeyError):
            raise StorageError('PG_SCHEMA_IMAGE_INVALID') from None


def verify_schema(expected, actual):
    if type(expected) is not SchemaImage or type(actual) is not SchemaImage:
        raise StorageError('PG_RESTORE_SCHEMA_PARITY_FAILED')
    for before, after in zip(expected.classes, actual.classes):
        if before != after:
            raise StorageError('PG_RESTORE_SCHEMA_PARITY_FAILED_' + before.kind.upper())
    if tuple(s.name for s in expected.sequences) != tuple(s.name for s in actual.sequences):
        raise StorageError('PG_RESTORE_SEQUENCE_SET_CHANGED')
    for before, after in zip(expected.sequences, actual.sequences):
        if after.last_value < before.last_value or after.last_value == before.last_value and before.is_called and not after.is_called:
            raise StorageError('PG_RESTORE_SEQUENCE_REWIND')


async def capture_schema(c, *, limits):
    if not c.is_in_transaction():
        raise StorageError('PG_SCHEMA_TRANSACTION_REQUIRED')
    await c.execute("""SELECT set_config('TimeZone','UTC',true),set_config('DateStyle','ISO,YMD',true),
        set_config('extra_float_digits','3',true),set_config('search_path','pg_catalog',true),set_config('statement_timeout',$1,true)""", str(limits.timeout_ms))
    # No unknown object owner or unmodelled relation/procedure is silently lost.
    unsafe = await c.fetchval("""SELECT EXISTS(SELECT 1 FROM pg_namespace n WHERE n.nspname=ANY($1::text[]) AND n.nspowner<>current_user::regrole)
        OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY($1::text[])
            AND (c.relowner<>n.nspowner OR c.relkind NOT IN ('r','p','S','v','i','I')))
        OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=ANY($1::text[])
            AND (p.proowner<>n.nspowner OR p.prokind<>'f'))
        OR EXISTS(SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname=ANY($1::text[])
            AND (t.typowner<>n.nspowner OR NOT(t.typtype IN ('d','e') OR t.typtype='c' AND EXISTS(SELECT 1 FROM pg_class r WHERE r.oid=t.typrelid AND r.relkind IN ('r','p','v')) OR t.typelem<>0 AND t.typtype='b')))
        OR EXISTS(SELECT 1 FROM pg_collation x JOIN pg_namespace n ON n.oid=x.collnamespace WHERE n.nspname=ANY($1::text[]))
        OR EXISTS(SELECT 1 FROM pg_operator x JOIN pg_namespace n ON n.oid=x.oprnamespace WHERE n.nspname=ANY($1::text[]))
        OR EXISTS(SELECT 1 FROM pg_opclass x JOIN pg_namespace n ON n.oid=x.opcnamespace WHERE n.nspname=ANY($1::text[]))
        OR EXISTS(SELECT 1 FROM pg_opfamily x JOIN pg_namespace n ON n.oid=x.opfnamespace WHERE n.nspname=ANY($1::text[]))
        OR EXISTS(SELECT 1 FROM pg_conversion x JOIN pg_namespace n ON n.oid=x.connamespace WHERE n.nspname=ANY($1::text[]))
        OR EXISTS(SELECT 1 FROM pg_ts_config x JOIN pg_namespace n ON n.oid=x.cfgnamespace WHERE n.nspname=ANY($1::text[]))
        OR EXISTS(SELECT 1 FROM pg_ts_dict x JOIN pg_namespace n ON n.oid=x.dictnamespace WHERE n.nspname=ANY($1::text[]))
        OR EXISTS(SELECT 1 FROM pg_ts_parser x JOIN pg_namespace n ON n.oid=x.prsnamespace WHERE n.nspname=ANY($1::text[]))
        OR EXISTS(SELECT 1 FROM pg_ts_template x JOIN pg_namespace n ON n.oid=x.tmplnamespace WHERE n.nspname=ANY($1::text[]))
        OR EXISTS(SELECT 1 FROM pg_default_acl d JOIN pg_namespace n ON n.oid=d.defaclnamespace
            WHERE n.nspname=ANY($1::text[]) AND d.defaclrole<>n.nspowner)
        OR EXISTS(SELECT 1 FROM pg_namespace n WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname NOT IN ('information_schema','public') AND NOT n.nspname=ANY($1::text[]))
        OR EXISTS(SELECT 1 FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname='public')
        OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public')
        OR EXISTS(SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public')
        OR EXISTS(SELECT 1 FROM pg_extension WHERE extname<>'plpgsql')
        OR EXISTS(SELECT 1 FROM pg_event_trigger)
        OR EXISTS(SELECT 1 FROM pg_publication)
        OR EXISTS(SELECT 1 FROM pg_subscription)
        OR EXISTS(SELECT 1 FROM pg_seclabel WHERE classoid IN ('pg_namespace'::regclass,'pg_class'::regclass,'pg_proc'::regclass,'pg_type'::regclass))""", list(NAMESPACES))
    if unsafe:
        raise StorageError('PG_SCHEMA_OWNER_OR_OBJECT_UNSUPPORTED')
    classes, total, count = [], 0, 0
    for kind, query in sorted(CATALOG.items()):
        sha, objects, size = hashlib.sha256(), 0, 0
        query = 'SELECT name,CASE WHEN octet_length(body::text)<=$2 THEN body::text END AS body FROM (' + query + ') q ORDER BY name COLLATE "C" LIMIT $3'
        async for row in c.cursor(query, list(NAMESPACES), limits.max_row_bytes, limits.max_schema_objects + 1, prefetch=16):
            count += 1
            if row['body'] is None or count > limits.max_schema_objects:
                raise StorageError('PG_SCHEMA_CAPACITY_EXCEEDED')
            raw = json.dumps([row['name'], row['body']], ensure_ascii=False, separators=(',', ':')).encode()
            total += len(raw)
            if total > limits.max_schema_bytes:
                raise StorageError('PG_SCHEMA_CAPACITY_EXCEEDED')
            sha.update(len(raw).to_bytes(8, 'big'))
            sha.update(raw)
            objects, size = objects + 1, size + len(raw)
        classes.append(CatalogClass(kind, objects, size, sha.hexdigest()))
    rows = await c.fetch("""SELECT quote_ident(n.nspname)||'.'||quote_ident(r.relname) AS name,s.seqincrement,s.seqcycle,
        quote_ident(tn.nspname)||'.'||quote_ident(t.relname) AS table_name,quote_ident(a.attname) AS column_name
        FROM pg_sequence s JOIN pg_class r ON r.oid=s.seqrelid JOIN pg_namespace n ON n.oid=r.relnamespace
        LEFT JOIN pg_depend d ON d.classid='pg_class'::regclass AND d.objid=r.oid AND d.refclassid='pg_class'::regclass AND d.deptype IN ('a','i')
        LEFT JOIN pg_class t ON t.oid=d.refobjid LEFT JOIN pg_namespace tn ON tn.oid=t.relnamespace
        LEFT JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=d.refobjsubid
        WHERE n.nspname=ANY($1::text[]) ORDER BY (quote_ident(n.nspname)||'.'||quote_ident(r.relname)) COLLATE "C" LIMIT $2""", list(NAMESPACES), limits.max_schema_objects + 1)
    if len(rows) > limits.max_schema_objects:
        raise StorageError('PG_SCHEMA_CAPACITY_EXCEEDED')
    sequences = []
    for row in rows:
        if row['seqincrement'] != 1 or row['seqcycle'] or row['table_name'] is None or row['column_name'] is None:
            raise StorageError('PG_SEQUENCE_PROFILE_UNSUPPORTED')
        value = await c.fetchrow('SELECT last_value,is_called FROM ' + row['name'])
        used = await c.fetchval('SELECT max(' + row['column_name'] + ') FROM ' + row['table_name'])
        if used is not None and (value['last_value'] < used or value['last_value'] == used and not value['is_called']):
            raise StorageError('PG_SEQUENCE_REWIND')
        sequences.append(SequenceState(row['name'], value['last_value'], value['is_called']))
    return SchemaImage(tuple(classes), tuple(sequences))
