"""Offline consistency review of the deployment artifact; no cloud API calls."""
import argparse
import json
import re
from pathlib import Path

import yaml


def require(condition, message="Deployment configuration is inconsistent with the reviewed service contract."):
    if not condition:
        raise ValueError(message)


def review(path, *, template=False, evidence=None):
    content = Path(path).read_text()
    if not template and "REPLACE_" in content:
        raise ValueError("Fill every REPLACE_ placeholder before deployment.")
    service = yaml.safe_load(content)
    require(service['apiVersion'] == 'serving.knative.dev/v1' and service['kind'] == 'Service')
    revision = service["spec"]["template"]
    annotations = revision["metadata"]["annotations"]
    require(annotations['run.googleapis.com/cpu-throttling'] == 'false', 'Continuous CPU is required.')
    require(annotations['autoscaling.knative.dev/minScale'] == '1')
    require(annotations['autoscaling.knative.dev/maxScale'] == '1')
    container = revision["spec"]["containers"][0]
    if not template:
        require(re.fullmatch('[^\\s@]+@sha256:[0-9a-f]{64}', container['image']), 'Use the tested registry manifest digest.')
        require(evidence is not None, "Supply the passing offline E2E result.json before deployment.")
        result = json.loads(Path(evidence).read_text())
        require(result.get("passed") is True and result.get("architecture") == "amd64", "Offline E2E did not pass for amd64.")
        require(container["image"].split("@", 1)[1] == result.get("registry_manifest_digest"),
                "Deployment digest differs from the image exercised in offline E2E.")
        require(result.get("release_scope_complete") is True, "Complete offline release scope, including compatible image rollback, is required.")
        required = {"schema", "spot", "v2-spot", "v2-perpetual", "upgrade"}
        scenarios = result.get("scenarios", {})
        require(required <= scenarios.keys() and all(scenarios[name].get("passed") is True for name in required),
                "Every required offline release scenario must pass.")
    require(container['ports'][0]['containerPort'] == 8080)
    require('command' not in container and 'args' not in container, 'Keep the tested foreground image entrypoint.')
    startup, live = container["startupProbe"], container["livenessProbe"]
    require(startup['httpGet'] == {'path': '/startup', 'port': 8080})
    require(210 <= startup['periodSeconds'] * startup['failureThreshold'] <= 240)
    require(live['httpGet'] == {'path': '/live', 'port': 8080})
    require(live['timeoutSeconds'] >= 5)
    require('readinessProbe' not in container, 'Standby must pass deployment startup independently of trading readiness.')
    env = {item["name"]: item for item in container["env"]}
    require(env['ENABLE_AUTO_MIGRATION']['value'] in ('true', 'false'))
    require('PORT' not in env and 'HBOT_ENV_FILE' not in env and ('HBOT_OFFLINE_HTTP_ORIGIN' not in env))
    require('DATABASE_URL' in env)
    for name, item in env.items():
        if name == "DATABASE_URL" or re.match(r"HBOT_OKX_(PERPETUAL_)?(API_KEY|SECRET_KEY|PASSPHRASE)$", name):
            secret = item["valueFrom"]["secretKeyRef"]
            require(re.fullmatch('[1-9][0-9]*', secret['key']), 'Pin a numeric Secret Manager version.')
    require(revision['spec']['serviceAccountName'])
    return container["image"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("--template", action="store_true")
    parser.add_argument("--evidence")
    args = parser.parse_args()
    try:
        review(args.config, template=args.template, evidence=args.evidence)
    except (KeyError, ValueError) as error:
        raise SystemExit(f"Cloud Run configuration review failed: {error}") from None
    except yaml.YAMLError:
        raise SystemExit("Cloud Run configuration review failed: invalid YAML.") from None
    print("Offline deployment configuration review passed; cloud IAM/network/provisioning was not exercised.")


if __name__ == "__main__":
    main()
