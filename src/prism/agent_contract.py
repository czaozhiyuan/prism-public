"""Stable, transport-neutral Agent API v2 contract metadata."""

from copy import deepcopy

from .items import ITEM_FIELDS, TEST_TYPES, EXECUTION_STATUSES, test_fields
from .delivery_fields import DELIVERY_SCHEMAS
from .model_identity import MODEL_TOKEN_PATTERN
from .schema import BASIC_FIELDS, ENVIRONMENT_FIELDS, ORGANIZATION_FIELDS, PEOPLE_FIELDS, PICTURE_FIELDS, TOOL_FIELDS, TEST_ENVIRONMENT_FIELDS
from .template_ids import OUTPUT_PATH_PATTERN, TEMPLATE_ID_PATTERN
from .lifecycle import WORK_TYPES

AGENT_API_VERSION = 2
COMMIT_STATES = ("not_committed", "committed", "unknown")
STATUSES = ("success", "success_with_warnings", "partial_failure", "failed")

ERRORS = {
    "PROJECT_NOT_FOUND": (False, 4),
    "PROJECT_FORMAT_UNSUPPORTED": (False, 4),
    "PROJECT_LOAD_FAILED": (False, 4),
    "STORAGE_RECOVERY_REQUIRED": (False, 5),
    "PROJECT_LOCKED": (True, 6),
    "GENERATION_BUSY": (True, 6),
    "INVALID_ARGUMENT": (False, 2),
    "INVALID_FIELD": (False, 2),
    "DERIVED_FIELD_READONLY": (False, 2),
    "REVISION_CONFLICT": (False, 7),
    "MODULE_NOT_FOUND": (False, 4),
    "MODULE_NAME_CONFLICT": (False, 4),
    "MODULE_ID_CONFLICT": (False, 4),
    "MODULE_ORDER_INVALID": (False, 2),
    "MODULE_FILE_CLEANUP_FAILED": (False, 9),
    "MODEL_NOT_FOUND": (False, 4),
    "MODEL_INCOMPATIBLE": (False, 4),
    "TEMPLATE_NOT_FOUND": (False, 4),
    "TEMPLATE_ID_CONFLICT": (False, 4),
    "NO_TEMPLATES_SELECTED": (False, 4),
    "OUTPUT_NAME_CONFLICT": (False, 4),
    "VALIDATION_FAILED": (False, 3),
    "GENERATION_FAILED": (False, 8),
    "WORD_UNAVAILABLE": (False, 8),
    "WORD_TIMEOUT": (True, 8),
    "WORD_FINALIZE_FAILED": (False, 8),
    "WORD_PAGINATION_UNSTABLE": (False, 8),
    "PATH_OUTSIDE_PROJECT": (False, 2),
    "OUTPUT_RECORD_SAVE_FAILED": (True, 9),
    "PROJECT_UNSCHEDULED": (False, 3),
}


def _object(properties=None, required=()):
    return {
        "type": "object",
        "properties": properties or {},
        "required": list(required),
        "additionalProperties": False,
    }


WARNING_SCHEMA = _object({
    "code": {"enum": list(ERRORS)}, "message": {"type": "string"}, "details": {"type": "object"},
}, ("code", "message", "details"))
ERROR_SCHEMA = _object({
    "code": {"enum": list(ERRORS)}, "message": {"type": "string"}, "retryable": {"type": "boolean"},
    "commit_state": {"enum": list(COMMIT_STATES)}, "details": {"type": "object"},
}, ("code", "message", "retryable", "commit_state", "details"))


TEXT = {"type": "string", "minLength": 1}
BOOL = {"type": "boolean"}
REVISION = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
MODULE_ID = {"type": "string", "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"}
MODEL_TOKEN_SCHEMA = {"type": "string", "pattern": MODEL_TOKEN_PATTERN}
TEMPLATE_ID = {"type": "string", "pattern": TEMPLATE_ID_PATTERN}
PROJECT = {"type": "string", "minLength": 1, "description": "Absolute project directory or 基本数据.json path."}
MODEL_REF = _object({"id": MODEL_TOKEN_SCHEMA, "version": MODEL_TOKEN_SCHEMA}, ("id", "version"))
TEMPLATE_CONFIG = _object({
    "mode": {"enum": ["default", "custom"]},
    "enabled_ids": {"type": "array", "items": TEMPLATE_ID, "uniqueItems": True},
    "document_versions": {"type": "object", "propertyNames": {"allOf": [TEMPLATE_ID, {"pattern": r"[\s\S]*\.[dD][oO][cC][xX]$"}]},
                          "additionalProperties": {"type": "string", "minLength": 1,
                                                   "pattern": r"^(?=[\s\S]*\S)[^\x00-\x1f\x7f-\x9f\u2028\u2029]+(?![\s\S])"}},
}, ("mode", "enabled_ids"))
FIELD_PATCH = _object({name: {"type": ["string", "number", "boolean", "null"]}
                       for name in BASIC_FIELDS + ORGANIZATION_FIELDS + PEOPLE_FIELDS + ENVIRONMENT_FIELDS})
FIELD_PATCH["properties"].update({name: {"type": "string"} for name in PICTURE_FIELDS})
FIELD_PATCH["properties"].update({name: {"type": "string"} for name in TOOL_FIELDS})
FIELD_PATCH["properties"]["调试器"] = {"type": "string"}
FIELD_PATCH["properties"].update({name: {"type": "string"} for name in TEST_ENVIRONMENT_FIELDS})
FIELD_PATCH["properties"].update({name: {"type": "string"} for name in PEOPLE_FIELDS})
FIELD_PATCH["properties"].update(deepcopy(DELIVERY_SCHEMAS))
SCHEDULE_PATCH = _object({
    "project_start": {"type": ["string", "null"], "format": "date"},
    "work_hours": {"type": "object", "propertyNames": MODEL_TOKEN_SCHEMA,
                       "additionalProperties": {"type": "integer", "minimum": 1}},
})
TEST_PROPERTIES = {}
for _test_kind, _label in TEST_TYPES:
    TEST_PROPERTIES[_test_kind] = {
        "type": "array",
        "items": _object({name: {"type": "string"} for name, _ in test_fields(_test_kind)}),
    }
    TEST_PROPERTIES[_test_kind]["items"]["properties"].update({
        "execution_status": {"type": "string", "enum": list(EXECUTION_STATUSES),
                             "default": "not_recorded"},
        "actual_date": {"type": "string", "pattern": r"^(?:[0-9]{4}-[0-9]{2}-[0-9]{2})?$"},
    })
MODULE_KINDS = {
    "feature": "features",
    "performance": "performances",
    "interface": "interfaces",
    "reliability": "reliabilities",
}
MODULE_NAME = {"type": "string", "pattern": "^.*\\S.*$"}


def _module_data_properties(kind):
    properties = {name: {"type": "string"} for name, _ in ITEM_FIELDS[kind]}
    properties["name"] = MODULE_NAME
    properties.update(TEST_PROPERTIES)
    return properties


MODULE_DATA_PROPERTIES = {}
for _kind in MODULE_KINDS.values():
    MODULE_DATA_PROPERTIES.update(_module_data_properties(_kind))
MODULE_PATCH_DATA = _object(MODULE_DATA_PROPERTIES)
MODULE_CREATE_DATA = {
    data_type: _object(_module_data_properties(kind), ("name",))
    for data_type, kind in MODULE_KINDS.items()
}
DATA_TYPE = {"enum": list(MODULE_KINDS)}
MODULE_CREATE_ENTRY = {
    "oneOf": [
        _object({"data_type": {"const": data_type}, "data": MODULE_CREATE_DATA[data_type]},
                ("data_type", "data"))
        for data_type in MODULE_KINDS
    ]
}
MODULE_BATCH = _object({
    "remove": {"type": "array", "items": _object({"module_id": MODULE_ID, "delete_file": BOOL}, ("module_id",))},
    "update": {"type": "array", "items": _object({"module_id": MODULE_ID, "data": MODULE_PATCH_DATA},
                                                       ("module_id", "data"))},
    "create": {"type": "array", "items": MODULE_CREATE_ENTRY},
    "reorder": {"type": "array", "items": _object({"data_type": DATA_TYPE,
                                                        "module_ids": {"type": "array", "items": MODULE_ID,
                                                                       "uniqueItems": True}},
                                                       ("data_type", "module_ids"))},
})


REQUEST_SCHEMAS = {
    "prism_info": _object(),
    "schema_get": _object({"name": TEXT}),
    "project_describe_fields": _object(),
    "project_create": _object({"project": PROJECT, "name": TEXT, "model": MODEL_REF, "dry_run": BOOL,
                               "templates": TEMPLATE_CONFIG},
                              ("project", "name")),
    "project_get": _object({"project": PROJECT}, ("project",)),
    "project_rename": _object({"project": PROJECT, "name": MODULE_NAME,
                               "expected_revision": REVISION, "dry_run": BOOL}, ("project", "name")),
    "project_set_fields": _object({"project": PROJECT, "fields": FIELD_PATCH, "expected_revision": REVISION,
                                   "dry_run": BOOL}, ("project", "fields")),
    "project_import_group": _object({"project": PROJECT, "source": TEXT, "expected_revision": REVISION,
                                     "dry_run": BOOL}, ("project", "source", "expected_revision")),
    "project_apply": _object({
        "project": PROJECT, "fields": FIELD_PATCH, "schedule": SCHEDULE_PATCH,
        "model": MODEL_REF, "templates": TEMPLATE_CONFIG, "modules": MODULE_BATCH,
        "expected_revision": REVISION, "dry_run": BOOL,
    }, ("project",)),
    "project_validate": _object({"project": PROJECT}, ("project",)),
    "project_recalculate": _object({"project": PROJECT, "expected_revision": REVISION, "dry_run": BOOL}, ("project",)),
    "project_generate": _object({"project": PROJECT, "expected_revision": REVISION,
                                 "mode": {"enum": ["enabled"]},
                                 "template_ids": {"type": "array", "items": TEMPLATE_ID, "uniqueItems": True},
                                 "dry_run": BOOL}, ("project",)),
    "module_list": _object({"project": PROJECT,
                            "data_type": DATA_TYPE},
                           ("project",)),
    "module_get": _object({"project": PROJECT, "module_id": MODULE_ID}, ("project", "module_id")),
    "module_create": {"oneOf": [
        _object({"project": PROJECT, "data_type": {"const": data_type},
                 "data": MODULE_CREATE_DATA[data_type], "expected_revision": REVISION, "dry_run": BOOL},
                ("project", "data_type", "data"))
        for data_type in MODULE_KINDS
    ]},
    "module_update": _object({"project": PROJECT, "module_id": MODULE_ID, "data": MODULE_PATCH_DATA,
                              "expected_revision": REVISION, "dry_run": BOOL}, ("project", "module_id", "data")),
    "module_remove": _object({"project": PROJECT, "module_id": MODULE_ID, "delete_file": BOOL, "expected_revision": REVISION,
                              "dry_run": BOOL}, ("project", "module_id")),
    "module_reorder": _object({"project": PROJECT, "data_type": DATA_TYPE,
                               "module_ids": {"type": "array", "items": MODULE_ID, "uniqueItems": True},
                               "expected_revision": REVISION, "dry_run": BOOL},
                              ("project", "data_type", "module_ids")),
    "model_list": _object(),
    "model_get": _object({"id": MODEL_TOKEN_SCHEMA, "version": MODEL_TOKEN_SCHEMA}, ("id", "version")),
    "project_set_model": _object({"project": PROJECT, "model": MODEL_REF, "expected_revision": REVISION,
                                  "dry_run": BOOL}, ("project", "model")),
    "template_list": _object({"project": PROJECT}),
    "template_configure": _object({"project": PROJECT, "templates": TEMPLATE_CONFIG,
                                   "expected_revision": REVISION, "dry_run": BOOL}, ("project", "templates")),
}

SCALAR = {"type": ["string", "number", "boolean", "null"]}
FIELDS_SCHEMA = {"type": "object", "additionalProperties": SCALAR}
TEST_ROW_SCHEMA = _object({name: {"type": "string"}
                           for kind, _ in TEST_TYPES for name, _ in test_fields(kind)})
TEST_ROW_SCHEMA["properties"].update({name: TEST_PROPERTIES["unit_tests"]["items"]["properties"][name]
                                      for name in ("execution_status", "actual_date")})
TEST_ROW_SCHEMA["properties"]["actual_executor"] = {
    "type": "string", "readOnly": True, "deprecated": True,
    "description": "Legacy stored value only; generated personnel come from project roles.",
}
TEST_ARRAY_SCHEMA = {"type": "array", "items": TEST_ROW_SCHEMA}
READ_TEST_PROPERTIES = deepcopy(TEST_PROPERTIES)
for _test_kind, _label in TEST_TYPES:
    READ_TEST_PROPERTIES[_test_kind]["items"]["properties"]["actual_executor"] = (
        deepcopy(TEST_ROW_SCHEMA["properties"]["actual_executor"]))
PHASE_RESULT_SCHEMA = _object({
    "id": TEXT, "name": TEXT, "duration": {"type": "integer", "minimum": 1},
    "start": {"type": "string", "format": "date"}, "finish": {"type": "string", "format": "date"},
}, ("id", "name", "duration", "start", "finish"))
WORK_RESULT_SCHEMA = deepcopy(PHASE_RESULT_SCHEMA)
WORK_RESULT_SCHEMA["properties"]["phase_id"] = TEXT
WORK_RESULT_SCHEMA["required"].append("phase_id")
WORK_RESULT_SCHEMA['properties'].update({
    'hours': {'type':'integer','minimum':1}, 'work_type': {'enum':list(WORK_TYPES)},
    'owner_role': {'enum':PEOPLE_FIELDS},
    'executor_roles': {'type':'array','minItems':1,'uniqueItems':True,'items':{'enum':PEOPLE_FIELDS}},
})
WORK_RESULT_SCHEMA['required'].extend(('hours','work_type','owner_role','executor_roles'))
SCHEDULE_SCHEMA = _object({
    "project_start": {"type": "string", "format": "date"},
    "work_hours": {"type": "object", "propertyNames": MODEL_TOKEN_SCHEMA,
                       "additionalProperties": {"type": "integer", "minimum": 1}},
    "dates": {"type": "object", "additionalProperties": {"type": "string"}, "readOnly": True},
    "phases": {"type": "array", "items": PHASE_RESULT_SCHEMA, "readOnly": True},
    "work_items": {"type": "array", "items": WORK_RESULT_SCHEMA, "readOnly": True},
})
SCHEDULE_SCHEMA['allOf'] = [{
    'if': {'not': {'required': ['project_start']}},
    'then': {'properties': {'dates': {'maxProperties': 0}, 'phases': {'maxItems': 0}, 'work_items': {'maxItems': 0}}},
}]
OUTPUT_RESULT_SCHEMA = _object({
    "template_id": TEMPLATE_ID,
    "output": {"oneOf": [{"type": "null"}, {"type": "string", "pattern": OUTPUT_PATH_PATTERN}]},
    "render_status": {"enum": ["success", "failed"]},
    "word_finalize_status": {"enum": ["success", "failed", "unavailable", "timeout", "unstable", "not_applicable"]},
    "message": {"type": "string"}, "warnings": {"type": "array", "items": {"type": "string"}},
}, ("template_id", "output", "render_status", "word_finalize_status", "message", "warnings"))
OUTPUT_RESULT_SCHEMA["allOf"] = [{
    "if": {"properties": {"render_status": {"const": "failed"}}},
    "then": {"properties": {"output": {"type": "null"}}},
    "else": {"properties": {"output": {"type": "string", "pattern": OUTPUT_PATH_PATTERN}}},
}]
OUTPUTS_SCHEMA = {
    "oneOf": [
        {"type": "object", "maxProperties": 0},
        _object({"source_revision": REVISION,
                 "time": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}$"},
                 "results": {"type": "array", "items": OUTPUT_RESULT_SCHEMA}},
                ("source_revision", "time", "results")),
    ]
}
MODULE_SCHEMA = {
    "oneOf": [
        _object({
            "module_format_version": {"const": 1}, "data_type": {"const": data_type},
            "module_id": MODULE_ID, "order": {"type": "integer", "minimum": 0},
            **_module_data_properties(kind),
            **READ_TEST_PROPERTIES,
        }, ("module_format_version", "data_type", "module_id", "order", "name"))
        for data_type, kind in MODULE_KINDS.items()
    ]
}
PROJECT_STATE_SCHEMA = _object({
    "format_version": {"const": 7}, "data_type": {"const": "basic"}, "name": TEXT,
    "model": MODEL_REF, "fields": FIELDS_SCHEMA, "schedule": SCHEDULE_SCHEMA,
    "templates": TEMPLATE_CONFIG, "outputs": OUTPUTS_SCHEMA,
    "modules": {"type": "array", "items": MODULE_SCHEMA},
}, ("format_version", "data_type", "name", "model", "fields", "schedule", "templates", "outputs", "modules"))
PHASE_SCHEMA = _object({"id": MODEL_TOKEN_SCHEMA, "name": TEXT,
                        "default_workdays": {"type": "integer", "minimum": 1}},
                       ("id", "name", "default_workdays"))
WORK_SCHEMA = _object({"id": MODEL_TOKEN_SCHEMA, "phase_id": MODEL_TOKEN_SCHEMA, "name": TEXT,
                       "default_hours": {"type":"integer","minimum":1},
                       "default_workdays": {"type": "integer", "minimum": 1, "readOnly":True},
                       "work_type": {'enum':list(WORK_TYPES)}, 'owner_role': {'enum':PEOPLE_FIELDS},
                       'executor_roles': {'type':'array','minItems':1,'uniqueItems':True,'items':{'enum':PEOPLE_FIELDS}}},
                      ("id", "phase_id", "name", "default_hours", "default_workdays", "work_type", "owner_role", "executor_roles"))
DATE_NODE_SCHEMA = _object({
    "key": TEXT, "work_id": MODEL_TOKEN_SCHEMA, "anchor": {"enum": ["start", "finish"]},
    "formats": {"type": "array", "items": {"type": "string"}},
}, ("key", "work_id", "anchor", "formats"))
MODEL_SCHEMA = _object({
    "format_version": {"const": 3}, "id": MODEL_TOKEN_SCHEMA, "version": MODEL_TOKEN_SCHEMA,
    "name": TEXT, "source": {"enum": ["bundled", "user"]},
    "phases": {"type": "array", "minItems": 1, "items": PHASE_SCHEMA},
    "works": {"type": "array", "minItems": 1, "items": WORK_SCHEMA},
    "date_nodes": {"type": "array", "items": DATE_NODE_SCHEMA},
}, ("format_version", "id", "version", "name", "source", "phases", "works", "date_nodes"))
TEMPLATE_SCHEMA = _object({
    "template_id": TEMPLATE_ID, "kind": {"enum": ["DOCX", "XLSX"]},
    "source": {"enum": ["asset", "model"]}, "enabled": BOOL,
    "document_version": {"type": ["string", "null"]},
}, ("template_id", "kind", "source", "enabled", "document_version"))
DIFF_SCHEMA = _object({
    "changed_paths": {"type": "array", "items": TEXT, "uniqueItems": True},
    "before_revision": REVISION, "after_revision": REVISION,
}, ("changed_paths", "before_revision", "after_revision"))
VALIDATION_ISSUE_SCHEMA = _object({
    "code": TEXT, "severity": {"enum": ["error", "warning"]}, "message": {"type": "string"},
    "path": {"type": "string"},
}, ("code", "severity", "message", "path"))
GENERATION_RESULT_SCHEMA = _object({
    "template_id": TEMPLATE_ID,
    "output": {"oneOf": [{"type": "null"}, {"type": "string", "pattern": OUTPUT_PATH_PATTERN}]},
    "render_status": {"enum": ["success", "failed"]},
    "word_finalize_status": {"enum": ["success", "failed", "unavailable", "timeout", "unstable", "not_applicable"]},
    "message": {"type": "string"}, "warnings": {"type": "array", "items": WARNING_SCHEMA},
}, ("template_id", "output", "render_status", "word_finalize_status", "message", "warnings"))
GENERATION_RESULT_SCHEMA["allOf"] = [{
    "if": {"properties": {"render_status": {"const": "failed"}}},
    "then": {"properties": {"output": {"type": "null"}}},
    "else": {"properties": {"output": {"type": "string", "pattern": OUTPUT_PATH_PATTERN}}},
}]
EXPECTED_OUTPUT_SCHEMA = _object({"template_id": TEMPLATE_ID,
                                  "output": {"type": "string", "pattern": OUTPUT_PATH_PATTERN}},
                                 ("template_id", "output"))
FIELD_DESCRIPTOR_SCHEMA = _object({
    "name": TEXT, "label": TEXT, "type": TEXT, "writable": BOOL,
    "nullable": BOOL, "derived": BOOL,
}, ("name", "type", "writable"))
FIELD_SECTION_SCHEMA = _object({
    "id": TEXT, "title": TEXT, "fields": {"type": "array", "items": FIELD_DESCRIPTOR_SCHEMA},
}, ("id", "title", "fields"))
SCHEDULE_FIELD_DESCRIPTOR_SCHEMA = _object({"writable": BOOL, "type": TEXT, "derived": BOOL, "nullable": BOOL},
                                           ("writable",))
MODULE_TYPE_DESCRIPTION_SCHEMA = _object({
    "label": TEXT, "fields": {"type": "array", "items": FIELD_DESCRIPTOR_SCHEMA},
    "test_collections": {"type": "object", "additionalProperties": {
        "type": "array", "items": FIELD_DESCRIPTOR_SCHEMA}},
}, ("label", "fields", "test_collections"))
FIELD_DESCRIPTION_SCHEMA = _object({
    "field_sections": {"type": "array", "items": FIELD_SECTION_SCHEMA},
    "schedule": _object({name: SCHEDULE_FIELD_DESCRIPTOR_SCHEMA
                         for name in ("project_start", "work_hours", "dates", "phases", "work_items")},
                        ("project_start", "work_hours", "dates", "phases", "work_items")),
    "outputs": _object({"writable": BOOL}, ("writable",)),
    "modules": {"type": "object", "additionalProperties": MODULE_TYPE_DESCRIPTION_SCHEMA},
}, ("field_sections", "schedule", "outputs", "modules"))
SCHEMA_GET_RESULT_SCHEMA = {
    "oneOf": [
        _object({"schemas": {"type": "array", "items": TEXT}}, ("schemas",)),
        _object({"name": TEXT, "schema": {"type": "object"}}, ("name", "schema")),
    ]
}
PRISM_INFO_SCHEMA = _object({
    "product_version": TEXT, "build_commit": TEXT, "agent_api_version": {"const": AGENT_API_VERSION},
    "project_format_version": {"type": "integer"}, "module_format_version": {"type": "integer"},
    "model_format_version": {"type": "integer"}, "assets_manifest": REVISION,
}, ("product_version", "build_commit", "agent_api_version", "project_format_version",
    "module_format_version", "model_format_version", "assets_manifest"))
TOOL_RESULT_SCHEMAS = {
    "prism_info": PRISM_INFO_SCHEMA,
    "schema_get": SCHEMA_GET_RESULT_SCHEMA,
    "project_describe_fields": FIELD_DESCRIPTION_SCHEMA,
    "project_create": _object({"changed": BOOL, "project": PROJECT_STATE_SCHEMA}, ("changed", "project")),
    "project_get": _object({"project": PROJECT_STATE_SCHEMA}, ("project",)),
    "project_rename": _object({"changed": BOOL, "diff": DIFF_SCHEMA}, ("changed", "diff")),
    "project_set_fields": _object({"changed": BOOL, "diff": DIFF_SCHEMA}, ("changed", "diff")),
    "project_import_group": _object({"changed": BOOL, "diff": DIFF_SCHEMA, "will_write": BOOL,
        "data_type": {"enum": ["people", "test_environment"]},
        "changes": {"type": "array", "items": _object({"field": TEXT,
            "before": {"type": ["string", "null"]}, "after": {"type": "string"}},
            ("field", "before", "after"))}}, ("changed", "diff", "will_write", "data_type", "changes")),
    "project_apply": _object({"changed": BOOL, "diff": DIFF_SCHEMA,
                              "created_modules": {"type": "array", "items": MODULE_SCHEMA}},
                             ("changed", "diff", "created_modules")),
    "project_validate": _object({"valid": BOOL, "issues": {"type": "array", "items": VALIDATION_ISSUE_SCHEMA}},
                                ("valid", "issues")),
    "project_recalculate": _object({"changed": BOOL, "schedule": SCHEDULE_SCHEMA}, ("changed", "schedule")),
    "project_generate": _object({"preflight": BOOL,
                                 "expected_outputs": {"type": "array", "items": EXPECTED_OUTPUT_SCHEMA},
                                 "results": {"type": "array", "items": GENERATION_RESULT_SCHEMA}},
                                ("preflight", "expected_outputs", "results")),
    "module_list": _object({"modules": {"type": "array", "items": MODULE_SCHEMA}}, ("modules",)),
    "module_get": _object({"module": MODULE_SCHEMA}, ("module",)),
    "module_create": _object({"changed": BOOL, "module": MODULE_SCHEMA}, ("changed", "module")),
    "module_update": _object({"changed": BOOL, "module": MODULE_SCHEMA}, ("changed", "module")),
    "module_remove": _object({"changed": BOOL, "module_id": MODULE_ID}, ("changed", "module_id")),
    "module_reorder": _object({"changed": BOOL, "module_ids": {"type": "array", "items": MODULE_ID}},
                              ("changed", "module_ids")),
    "model_list": _object({"models": {"type": "array", "items": MODEL_SCHEMA}}, ("models",)),
    "model_get": _object({"model": MODEL_SCHEMA}, ("model",)),
    "project_set_model": _object({"changed": BOOL, "model": MODEL_REF, "schedule": SCHEDULE_SCHEMA},
                                 ("changed", "model", "schedule")),
    "template_list": _object({"templates": {"type": "array", "items": TEMPLATE_SCHEMA}}, ("templates",)),
    "template_configure": _object({"changed": BOOL, "templates": TEMPLATE_CONFIG}, ("changed", "templates")),
}

IMPLEMENTED_TOOLS = (
    "prism_info", "schema_get", "project_describe_fields", "project_get", "module_list",
    "module_get", "model_list", "model_get", "template_list", "project_create",
    "project_rename", "project_set_fields", "project_import_group", "project_apply", "project_validate", "project_recalculate", "module_create",
    "module_update", "module_remove", "module_reorder", "project_set_model", "template_configure",
    "project_generate",
)

RESPONSE_SCHEMA = _object({
    "ok": BOOL,
    "status": {"enum": list(STATUSES)},
    "data": {},
    "warnings": {"type": "array", "items": WARNING_SCHEMA},
    "revision": {"oneOf": [{"type": "null"}, REVISION]},
    "commit_state": {"enum": list(COMMIT_STATES)},
    "records_saved": {"type": ["boolean", "null"]},
    "review_checklist": {"oneOf": [{"type": "null"},
                                      {"type": "string", "pattern": OUTPUT_PATH_PATTERN}]},
    "error": {"oneOf": [{"type": "null"}, ERROR_SCHEMA]},
}, ("ok", "status", "data", "warnings", "revision", "commit_state", "records_saved",
    "review_checklist", "error"))
RESPONSE_SCHEMA["oneOf"] = [
    {"properties": {"ok": {"const": True}, "status": {"const": "success"},
                    "warnings": {"maxItems": 0}, "error": {"type": "null"}}},
    {"properties": {"ok": {"const": True}, "status": {"const": "success_with_warnings"},
                    "warnings": {"minItems": 1}, "error": {"type": "null"}}},
    {"properties": {"ok": {"const": False}, "status": {"enum": ["partial_failure", "failed"]},
                    "error": ERROR_SCHEMA}},
]


GENERATION_STATE_TABLE = [
    {"case": "preflight_rejected", "status": "failed", "commit_state": "not_committed",
     "records_saved": False, "review_checklist": None},
    {"case": "unscheduled_preflight_rejected", "status": "failed", "commit_state": "not_committed",
     "records_saved": False, "review_checklist": None},
    {"case": "dry_run_preflight", "status": "success", "commit_state": "not_committed",
     "records_saved": False, "review_checklist": None},
    {"case": "all_render_failed", "status": "failed", "commit_state": "committed",
     "records_saved": True, "review_checklist": None},
    {"case": "partial_failure", "status": "partial_failure", "commit_state": "committed",
     "records_saved": True, "review_checklist": "current_batch_only"},
    {"case": "review_checklist_failed", "status": "partial_failure", "commit_state": "committed",
     "records_saved": True, "review_checklist": None},
    {"case": "success_with_word_warnings", "status": "success_with_warnings", "commit_state": "committed",
     "records_saved": True, "review_checklist": "current_batch_only"},
    {"case": "record_save_failed", "status": "failed", "commit_state": "committed",
     "records_saved": False, "review_checklist": "current_batch_only"},
]


_EXAMPLE_DOCX_ID = "asset:templates/docx/example.docx"
_EXAMPLE_XLSX_ID = "asset:reference/workbooks/example.xlsx"


def _example_generation_result(template_id, output, render_status, word_status="not_applicable", warnings=()):
    return {
        "template_id": template_id, "output": output, "render_status": render_status,
        "word_finalize_status": word_status, "message": "", "warnings": list(warnings),
    }


def _example_generation_data(results, expected_outputs=(), preflight=False):
    return {"preflight": preflight, "expected_outputs": list(expected_outputs), "results": results}


GENERATION_EXAMPLES = {
    "preflight_rejected": {
        "ok": False, "status": "failed", "data": _example_generation_data([]),
        "warnings": [], "revision": None,
        "commit_state": "not_committed", "records_saved": False, "review_checklist": None,
        "error": {"code": "NO_TEMPLATES_SELECTED", "message": "No templates selected.",
                  "retryable": False, "commit_state": "not_committed", "details": {}},
    },
    "unscheduled_preflight_rejected": {
        "ok": False, "status": "failed", "data": _example_generation_data([]),
        "warnings": [], "revision": "sha256:" + "0" * 64,
        "commit_state": "not_committed", "records_saved": False, "review_checklist": None,
        "error": {"code": "PROJECT_UNSCHEDULED", "message": "Project is not scheduled.",
                  "retryable": False, "commit_state": "not_committed",
                  "details": {"stage": "preflight"}},
    },
    "dry_run_preflight_success": {
        "ok": True, "status": "success", "data": _example_generation_data([], [
            {"template_id": _EXAMPLE_DOCX_ID, "output": "outputs/example.docx"}], preflight=True),
        "warnings": [], "revision": "sha256:" + "0" * 64,
        "commit_state": "not_committed", "records_saved": False, "review_checklist": None,
        "error": None,
    },
    "all_render_failed_and_recorded": {
        "ok": False, "status": "failed", "data": _example_generation_data([
            _example_generation_result(_EXAMPLE_DOCX_ID, None, "failed")],
            [{"template_id": _EXAMPLE_DOCX_ID, "output": "outputs/example.docx"}]),
        "warnings": [], "revision": "sha256:" + "0" * 64, "commit_state": "committed",
        "records_saved": True, "review_checklist": None,
        "error": {"code": "GENERATION_FAILED", "message": "All renders failed.", "retryable": False,
                  "commit_state": "committed", "details": {}},
    },
    "partial_failure": {
        "ok": False, "status": "partial_failure", "data": _example_generation_data([
            _example_generation_result(_EXAMPLE_DOCX_ID, "outputs/example.docx", "success", "success"),
            _example_generation_result(_EXAMPLE_XLSX_ID, None, "failed"),
        ], [{"template_id": _EXAMPLE_DOCX_ID, "output": "outputs/example.docx"},
            {"template_id": _EXAMPLE_XLSX_ID, "output": "outputs/example.xlsx"}]),
        "warnings": [], "revision": "sha256:" + "0" * 64, "commit_state": "committed",
        "records_saved": True, "review_checklist": "outputs/待补充清单.xlsx",
        "error": {"code": "GENERATION_FAILED", "message": "One or more outputs failed.",
                  "retryable": False, "commit_state": "committed", "details": {}},
    },
    "review_checklist_failed": {
        "ok": False, "status": "partial_failure", "data": _example_generation_data([
            _example_generation_result(_EXAMPLE_DOCX_ID, "outputs/example.docx", "success", "success")],
            [{"template_id": _EXAMPLE_DOCX_ID, "output": "outputs/example.docx"}]),
        "warnings": [], "revision": "sha256:" + "0" * 64, "commit_state": "committed",
        "records_saved": True, "review_checklist": None,
        "error": {"code": "GENERATION_FAILED", "message": "Review checklist generation failed.",
                  "retryable": False, "commit_state": "committed",
                  "details": {"stage": "review_checklist"}},
    },
    "word_warning_success": {
        "ok": True, "status": "success_with_warnings", "data": _example_generation_data([
            _example_generation_result(_EXAMPLE_DOCX_ID, "outputs/example.docx", "success", "unavailable",
                                       [{"code": "WORD_UNAVAILABLE", "message": "Word unavailable.",
                                         "details": {}}])],
            [{"template_id": _EXAMPLE_DOCX_ID, "output": "outputs/example.docx"}]),
        "warnings": [{"code": "WORD_UNAVAILABLE", "message": "Word finalization unavailable.", "details": {}}],
        "revision": "sha256:" + "0" * 64,
        "commit_state": "committed", "records_saved": True,
        "review_checklist": "outputs/待补充清单.xlsx", "error": None,
    },
    "record_save_failed": {
        "ok": False, "status": "failed", "data": _example_generation_data([
            _example_generation_result(_EXAMPLE_DOCX_ID, "outputs/example.docx", "success", "success")],
            [{"template_id": _EXAMPLE_DOCX_ID, "output": "outputs/example.docx"}]),
        "warnings": [], "revision": "sha256:" + "0" * 64, "commit_state": "committed",
        "records_saved": False, "review_checklist": "outputs/待补充清单.xlsx",
        "error": {"code": "OUTPUT_RECORD_SAVE_FAILED", "message": "Outputs record save failed.",
                  "retryable": True, "commit_state": "committed", "details": {}},
    },
    "old_checklist_not_reused": {
        "ok": True, "status": "success", "data": _example_generation_data([
            _example_generation_result(_EXAMPLE_XLSX_ID, "outputs/example.xlsx", "success")],
            [{"template_id": _EXAMPLE_XLSX_ID, "output": "outputs/example.xlsx"}]),
        "warnings": [], "revision": "sha256:" + "0" * 64, "commit_state": "committed",
        "records_saved": True, "review_checklist": None, "error": None,
    },
}


RESPONSE_EXAMPLES = {
    "success": {"ok": True, "status": "success", "data": {"changed": False}, "warnings": [],
                "revision": "sha256:" + "0" * 64, "commit_state": "not_committed",
                "records_saved": None, "review_checklist": None, "error": None},
    "success_with_warnings": {
        "ok": True, "status": "success_with_warnings", "data": {"changed": True},
        "warnings": [{"code": "WORD_UNAVAILABLE", "message": "Word is unavailable.", "details": {}}],
        "revision": "sha256:" + "0" * 64, "commit_state": "committed", "records_saved": True,
        "review_checklist": None, "error": None,
    },
    "failed": {
        "ok": False, "status": "failed", "data": None, "warnings": [], "revision": None,
        "commit_state": "not_committed", "records_saved": None, "review_checklist": None,
        "error": {"code": "INVALID_ARGUMENT", "message": "Invalid request.", "retryable": False,
                  "commit_state": "not_committed", "details": {}},
    },
}


TRANSACTION_STATE_TABLE = [
    {"case": "rollback_succeeded", "ok": False, "status": "failed",
     "commit_state": "not_committed", "error": "original_error"},
    {"case": "rollback_failed", "ok": False, "status": "failed",
     "commit_state": "unknown", "error": "STORAGE_RECOVERY_REQUIRED"},
    {"case": "module_file_cleanup_failed", "ok": True, "status": "success_with_warnings",
     "commit_state": "committed", "warning": "MODULE_FILE_CLEANUP_FAILED"},
]


TRANSACTION_EXAMPLES = {
    "rollback_succeeded": {
        "ok": False, "status": "failed", "data": None, "warnings": [], "revision": None,
        "commit_state": "not_committed", "records_saved": None, "review_checklist": None,
        "error": {"code": "PROJECT_LOAD_FAILED", "message": "Project write failed.",
                  "retryable": False, "commit_state": "not_committed",
                  "details": {"rollback": "succeeded"}},
    },
    "rollback_failed": {
        "ok": False, "status": "failed", "data": None, "warnings": [], "revision": None,
        "commit_state": "unknown", "records_saved": None, "review_checklist": None,
        "error": {"code": "STORAGE_RECOVERY_REQUIRED", "message": "Rollback failed.",
                  "retryable": False, "commit_state": "unknown",
                  "details": {"recovery_materials_preserved": True}},
    },
    "module_file_cleanup_failed": {
        "ok": True, "status": "success_with_warnings",
        "data": {"changed": True, "module_id": "00000000-0000-0000-0000-000000000000"},
        "warnings": [{"code": "MODULE_FILE_CLEANUP_FAILED", "message": "Old module file cleanup failed.",
                      "details": {"path": "modules/old.json"}}],
        "revision": "sha256:" + "0" * 64, "commit_state": "committed",
        "records_saved": None, "review_checklist": None, "error": None,
    },
}


UNSCHEDULED_EXAMPLES = {
    "field_save": {
        "ok": True, "status": "success_with_warnings",
        "data": {"changed": True, "diff": {
            "changed_paths": ["fields.项目名"], "before_revision": "sha256:" + "0" * 64,
            "after_revision": "sha256:" + "1" * 64}},
        "warnings": [{"code": "PROJECT_UNSCHEDULED", "message": "Project remains unscheduled.",
                      "details": {}}],
        "revision": "sha256:" + "1" * 64, "commit_state": "committed",
        "records_saved": None, "review_checklist": None, "error": None,
    },
    "model_switch": {
        "ok": True, "status": "success_with_warnings",
        "data": {"changed": True, "model": {"id": "gjb5000-simplified", "version": "3.0"},
                 "schedule": {}},
        "warnings": [{"code": "PROJECT_UNSCHEDULED", "message": "Project remains unscheduled.",
                      "details": {}}],
        "revision": "sha256:" + "0" * 64, "commit_state": "committed",
        "records_saved": None, "review_checklist": None, "error": None,
    },
}


def _tool_response_schema(name):
    failure_data_schema = {"type": "null"}
    if name == "project_generate":
        failure_data_schema = {"oneOf": [{"type": "null"}, TOOL_RESULT_SCHEMAS[name]]}
    data_schema = {
        "if": {"properties": {"ok": {"const": True}}},
        "then": {"properties": {"data": TOOL_RESULT_SCHEMAS[name]}},
        "else": {"properties": {"data": failure_data_schema}},
    }
    return {"allOf": [RESPONSE_SCHEMA, data_schema]}


CONTRACT = {
    "agent_api_version": AGENT_API_VERSION,
    "compatibility": "Within v1, tools, optional request properties, and response properties may be added; existing semantics are stable.",
    "response_schema": RESPONSE_SCHEMA,
    "error_schema": ERROR_SCHEMA,
    "errors": {code: {"retryable_default": values[0], "cli_exit_code": values[1]}
               for code, values in ERRORS.items()},
    "cli_exit_rule": "Return 0 whenever response.ok is true, including warnings; otherwise use the nonzero mapping for response.error.code.",
    "tools": {name: {"implemented": name in IMPLEMENTED_TOOLS, "request_schema": schema,
                     "response_schema": _tool_response_schema(name),
                     "response_data_schema": TOOL_RESULT_SCHEMAS[name]}
              for name, schema in REQUEST_SCHEMAS.items()},
    "request_rules": {
        "unknown_properties": "rejected",
        "explicit_null": "rejected unless the property schema includes null",
        "empty_string": "rejected for identifiers and paths",
        "derived_fields": "schedule.dates, schedule.phases, and project date fields are read-only",
        "outputs": "read-only; no Agent request may set project outputs",
        "expected_revision": "optimistic precondition; required for project_import_group, optional for other writes; conflict fails before mutation",
        "dry_run": "validates and computes the candidate but never writes, reserves no IDs, and returns commit_state=not_committed",
        "no_op": "validates all inputs and expected_revision, writes no files, and does not change revision",
    },
    "parameter_defaults": {
        "dry_run": False,
        "project_generate.mode": "enabled when neither mode nor template_ids is provided",
        "project_create.model": {"id": "gjb5000-simplified", "version": "3.0"},
        "module_remove.delete_file": False,
        "expected_revision": "except project_import_group (required), omitted means apply to the latest snapshot loaded under the operation lock",
        "template_list.project": "omitted lists bundled templates without project selection state",
        "module_list.data_type": "omitted returns every category in canonical category and project order",
        "empty_project_apply": "valid no-op after project and expected_revision validation",
        "empty_arrays": "accepted only where an array property is optional and business rules do not require members",
    },
    "side_effect_classes": {
        "read_only": ["prism_info", "schema_get", "project_describe_fields", "project_get",
                      "module_list", "module_get", "model_list", "model_get", "template_list"],
        "business_transaction": ["project_create", "project_rename", "project_set_fields", "project_import_group", "project_apply", "project_recalculate",
                                 "module_create", "module_update", "module_remove", "module_reorder",
                                 "project_set_model", "template_configure"],
        "validation_only": ["project_validate"],
        "generation": ["project_generate"],
    },
    "validation_stages": ["request_schema", "path_and_identity", "concurrency_precondition",
                          "candidate_business_rules", "storage_preflight"],
    "project_apply_order": ["validate_request_and_conflicts", "model_then_schedule_then_fields",
                            "templates", "modules_remove_update_create_reorder",
                            "recalculate_validate_diff_single_commit"],
    "project_apply_conflicts": {
        "duplicate_target": "rejected", "remove_and_update_same_module": "rejected",
        "create_and_reorder_same_category": "unsupported and rejected in v1",
        "dry_run_created_ids": "temporary and never reserved",
    },
    "module_field_rules": {
        "create": "data must match the category selected by data_type",
        "update": "load the target module, validate data against its category, and return INVALID_FIELD for category-only fields from another module type",
        "name": {"omitted": "preserve on update", "empty": "INVALID_ARGUMENT",
                 "null": "INVALID_ARGUMENT", "value": "non-empty and not whitespace-only"},
        "other_text_fields": {"omitted": "preserve", "empty": "clear", "null": "INVALID_ARGUMENT"},
        "test_arrays": {"omitted": "preserve", "present": "replace_entire_array",
                        "null": "INVALID_ARGUMENT"},
    },
    "project_rename_rules": {
        "target": "top-level name only; preserve fields.项目名, project directory, modules and outputs",
        "name": "non-whitespace string; leading and trailing whitespace is trimmed",
    },
    "project_import_group_rules": {
        "project_format": 7, "group_format": 1,
        "source": "absolute JSON path; data_type selects people or test_environment, not the filename",
        "expected_revision": "required for preview and commit; commit uses the preview before_revision",
        "fields": "sparse string-only patch; missing preserves, explicit empty clears; cross-group and unknown keys rejected",
        "storage": "copy into the target fixed group file; no external binding or v6 migration",
        "preview": "changes include old/new values; diff includes before/after revision; dry_run never writes",
    },
    "project_create_initial_state": {
        "schedule": {}, "templates": {"mode": "default", "enabled_ids": []}, "outputs": {},
    },
    "unscheduled_project": {
        "save": "allowed_with_PROJECT_UNSCHEDULED_warning",
        "model_switch": "allowed_with_PROJECT_UNSCHEDULED_warning",
        "derived_dates": "empty_or_omitted",
        "derived_phases": "empty_or_omitted",
        "generation": "PROJECT_UNSCHEDULED_before_output_side_effects",
    },
    "schedule_validation": {
        "unknown_work_id": "INVALID_ARGUMENT", "null_duration": "INVALID_ARGUMENT",
        "non_positive_duration": "INVALID_ARGUMENT", "invalid_date": "INVALID_ARGUMENT",
    },
    "generation_selection": {
        "default": "mode=enabled",
        "mode": ["enabled"],
        "template_ids": "ordered logical IDs; bundled unit/simulation pairs route by device type and deduplicate",
        "device_test_documents": "FPGA selects simulation plan/description/report; MCU, unknown or empty selects unit tests; actual target IDs and independent versions appear in outputs",
        "conflict": "explicit mode and template_ids together return INVALID_ARGUMENT",
        "empty": "template_ids=[] or an enabled selection resolving to zero templates returns NO_TEMPLATES_SELECTED",
    },
    "template_selection_cases": [
        {"case": "duplicate_id", "outcome": "INVALID_ARGUMENT"},
        {"case": "valid_but_missing_id", "outcome": "TEMPLATE_NOT_FOUND"},
        {"case": "explicit_disabled_template", "outcome": "allowed_for_this_generation"},
        {"case": "explicit_opposite_device_test_template", "outcome": "routed_to_applicable_device_template"},
        {"case": "duplicate_output_name", "outcome": "OUTPUT_NAME_CONFLICT"},
        {"case": "empty_selection", "outcome": "NO_TEMPLATES_SELECTED"},
    ],
    "generation_checklist_failure": {
        "status": "partial_failure",
        "error": "GENERATION_FAILED",
        "details_stage": "review_checklist",
        "successful_outputs": "preserved",
        "record_save_failure_precedence": "OUTPUT_RECORD_SAVE_FAILED",
    },
    "model_discovery": {
        "roots": ["bundled assets/models", "user application data PRISM/models"],
        "identity": "model id and version from validated model.json",
        "conflict": "duplicate id/version across installed roots returns MODEL_INCOMPATIBLE",
    },
    "model_switch": {
        "preserve": ["project_start", "work_hours for stable work IDs shared by both models"],
        "initialize": "new work IDs use target model defaults",
        "discard": ["old-only work durations", "derived dates", "derived phases"],
        "recalculate": "when project_start exists; otherwise return a warning",
    },
    "generation_state_table": GENERATION_STATE_TABLE,
    "generation_examples": GENERATION_EXAMPLES,
    "transaction_state_table": TRANSACTION_STATE_TABLE,
    "transaction_examples": TRANSACTION_EXAMPLES,
    "unscheduled_examples": UNSCHEDULED_EXAMPLES,
    "response_examples": RESPONSE_EXAMPLES,
}


def contract_copy():
    """Return a caller-owned JSON-compatible contract document."""
    return deepcopy(CONTRACT)


def cli_exit_code(response):
    """Apply the frozen P8 adapter rule without interpreting warning codes."""
    if response.get("ok") is True:
        return 0
    error = response.get("error") or {}
    return ERRORS.get(error.get("code"), (False, 1))[1]
