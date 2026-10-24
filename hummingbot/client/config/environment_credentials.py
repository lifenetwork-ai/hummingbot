"""Environment input adapter for existing connector models; no secret persistence."""
import os
from typing import Iterable, Mapping, Optional

from pydantic import ValidationError

from hummingbot.client.config.config_helpers import ClientConfigAdapter, get_connector_hb_config
from hummingbot.client.config.runtime_config import RuntimeConfigurationError


def connector_environment_fields(connector: str):
    template = get_connector_hb_config(connector)
    if template is None:
        raise RuntimeConfigurationError(f"Connector {connector} has no environment credential model.")
    fields = [item for item in ClientConfigAdapter(template).traverse(secure=False)
              if item.client_field_data is not None and item.client_field_data.is_connect_key]
    return type(template), fields


def load_connector_credentials(connector: str, environ: Optional[Mapping[str, str]] = None) -> ClientConfigAdapter:
    values = os.environ if environ is None else environ
    model, fields = connector_environment_fields(connector)
    payload = {"connector": connector}
    missing = []
    for item in fields:
        variable = "HBOT_" + item.attr.upper()
        if variable in values:
            if not values[variable]:
                missing.append(variable)
            else:
                payload[item.attr] = values[variable]
        elif model.model_fields[item.attr].is_required():
            missing.append(variable)
    if missing:
        raise RuntimeConfigurationError("Missing connector environment values: " + ", ".join(missing))
    try:
        return ClientConfigAdapter(model.model_validate(payload))
    except ValidationError as error:
        # Pydantic errors can contain plaintext input, even for SecretStr. Display
        # only field locations, never its raw exception or errors' input values.
        locations = sorted({".".join(map(str, item["loc"])) for item in error.errors(include_input=False)})
        raise RuntimeConfigurationError(f"Invalid {connector} environment fields: {', '.join(locations)}") from None


def configured_environment_connectors(names: Iterable[str]) -> list[str]:
    configured = []
    for name in names:
        try:
            _, fields = connector_environment_fields(name)
        except RuntimeConfigurationError:
            continue
        if any("HBOT_" + field.attr.upper() in os.environ for field in fields):
            configured.append(name)
    return configured
