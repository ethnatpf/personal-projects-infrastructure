#!/usr/bin/python3
"""Pull-based personal project image deployment with persistent recovery state."""

import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def load_environment_file(path):
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = shlex.split(line, comments=True, posix=True)
        if parts and "=" in parts[0]:
            key, value = parts[0].split("=", 1)
            os.environ.setdefault(key, value)


if not os.environ.get("DEPLOY_NAME") and len(sys.argv) >= 3 and sys.argv[1] in ("clear-failed", "retry-rollback"):
    deployment_name = sys.argv[2]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", deployment_name):
        raise SystemExit("invalid deployment name")
    load_environment_file(Path("/etc/personal-project-deployer") / f"{deployment_name}.env")


REPO = Path(os.environ["DEPLOY_REPO"])
GIT_CHECKOUT = Path(os.environ["GIT_CHECKOUT"])
DEPLOY_NAME = os.environ["DEPLOY_NAME"]
IMAGE_NAME = os.environ["IMAGE_NAME"].removeprefix("ghcr.io/")
WATCH_TAG = os.environ["WATCH_TAG"]
BRANCH = os.environ["DEPLOY_BRANCH"]
VARIABLE_FILE = REPO / os.environ["IMAGE_VARIABLE_FILE"]
APP_DIRECTORY = Path(os.environ["APP_DIRECTORY"])
COMPOSE_SERVICE = os.environ["COMPOSE_SERVICE"]
STATE_FILE = Path(os.environ["STATE_FILE"])
LOCK_FILE = Path(os.environ["LOCK_FILE"])
ANSIBLE_PLAYBOOK = REPO / os.environ["ANSIBLE_PLAYBOOK"]
VAULT_PASSWORD_FILE = os.environ["VAULT_PASSWORD_FILE"]
GIT_REMOTE = os.environ["GIT_REMOTE"]
HEALTH_TIMEOUT = int(os.environ["HEALTH_TIMEOUT"])
ANSIBLE_REMOTE_TMP = os.environ["ANSIBLE_REMOTE_TMP"]
VAR_NAME = os.environ["IMAGE_VARIABLE_NAME"]
REF_RE = re.compile(r"^ghcr\.io/[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}$")
DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
IMAGE_NAME_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*$")
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
VARIABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class OperationalError(Exception):
    pass


def log(message):
    print(f"personal-project-deploy[{DEPLOY_NAME}]: {message}", flush=True)


def run(args, *, cwd=None, check=True, capture=True, env=None):
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise OperationalError(f"command failed ({result.returncode}): {args[0]}{': ' + detail if detail else ''}")
    return result


def load_state():
    if not STATE_FILE.exists():
        return {"successful_digest": "", "successful_ref": "", "failed_digests": [], "pending_git_sync": None, "in_progress": None}
    try:
        state = json.loads(STATE_FILE.read_text())
        state.setdefault("successful_digest", "")
        state.setdefault("successful_ref", "")
        state.setdefault("failed_digests", [])
        state.setdefault("pending_git_sync", None)
        state.setdefault("in_progress", None)
        return state
    except (OSError, json.JSONDecodeError) as exc:
        raise OperationalError(f"deployment state cannot be read safely: {exc}") from exc


def save_state(state):
    STATE_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".state-", dir=STATE_FILE.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, STATE_FILE)
        dir_fd = os.open(STATE_FILE.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def parse_image_variable():
    content = VARIABLE_FILE.read_text()
    name = re.escape(VAR_NAME)
    matches = re.findall(rf"(?m)^{name}:\s*([^\s#]+)\s*$", content)
    if len(matches) != 1:
        raise OperationalError(f"expected exactly one {VAR_NAME} entry in {VARIABLE_FILE}")
    image_ref_value = matches[0]
    if image_ref_value.startswith(f"ghcr.io/{IMAGE_NAME}@"):
        if not REF_RE.fullmatch(image_ref_value):
            raise OperationalError("tracked image contains an invalid digest reference")
    elif image_ref_value.startswith(f"ghcr.io/{IMAGE_NAME}:"):
        if not TAG_RE.fullmatch(image_ref_value.rsplit(":", 1)[1]):
            raise OperationalError("tracked image contains an invalid tag")
    else:
        raise OperationalError("tracked image is outside the configured GHCR repository")
    return image_ref_value


def set_image_variable(image_ref):
    if not REF_RE.fullmatch(image_ref):
        raise OperationalError("refusing to write an invalid immutable GHCR image reference")
    content = VARIABLE_FILE.read_text()
    updated, count = re.subn(
        rf"(?m)^{re.escape(VAR_NAME)}:\s*[^\s#]+\s*$",
        f"{VAR_NAME}: {image_ref}",
        content,
    )
    if count != 1:
        raise OperationalError(f"expected exactly one {VAR_NAME} entry in {VARIABLE_FILE}")
    temp = VARIABLE_FILE.with_name(VARIABLE_FILE.name + ".tmp")
    temp.write_text(updated)
    os.chmod(temp, VARIABLE_FILE.stat().st_mode & 0o777)
    os.replace(temp, VARIABLE_FILE)


def registry_token():
    query = urllib.parse.urlencode({"service": "ghcr.io", "scope": f"repository:{IMAGE_NAME}:pull"})
    request = urllib.request.Request(f"https://ghcr.io/token?{query}")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read())
        token = data.get("token") or data.get("access_token")
        if not token:
            raise OperationalError("GHCR token response did not contain a token")
        return token
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise OperationalError(f"GHCR authentication/registry request failed: {exc}") from exc


def resolve_digest(tag_or_digest):
    if DIGEST_RE.fullmatch(tag_or_digest):
        return tag_or_digest
    token = registry_token()
    url = f"https://ghcr.io/v2/{IMAGE_NAME}/manifests/{urllib.parse.quote(tag_or_digest, safe=':._-')}"
    request = urllib.request.Request(url, method="HEAD")
    request.add_header("Authorization", "Bearer " + token)
    request.add_header(
        "Accept",
        ", ".join([
            "application/vnd.oci.image.index.v1+json",
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.oci.image.manifest.v1+json",
            "application/vnd.docker.distribution.manifest.v2+json",
        ]),
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            digest = response.headers.get("Docker-Content-Digest", "")
        if not DIGEST_RE.fullmatch(digest):
            raise OperationalError("GHCR returned a missing or invalid image digest")
        return digest
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise OperationalError("GHCR tag does not exist") from exc
        raise OperationalError(f"GHCR manifest lookup failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise OperationalError(f"GHCR manifest lookup failed: {exc}") from exc


def image_ref(digest):
    ref = f"ghcr.io/{IMAGE_NAME}@{digest}"
    if not REF_RE.fullmatch(ref):
        raise OperationalError("configured GHCR image name or returned digest is invalid")
    return ref


def repo_status():
    return run(["git", "status", "--porcelain"], cwd=GIT_CHECKOUT).stdout.splitlines()


def git_env():
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = os.environ["GIT_ASKPASS"]
    env["GIT_ASKPASS_REQUIRE"] = "force"
    return env


def ensure_clean_repository():
    status = repo_status()
    if status:
        raise OperationalError("infrastructure checkout has local changes; refusing to pull or deploy: " + "; ".join(status[:8]))


def run_playbook():
    command = [
        "/usr/bin/ansible-playbook",
        "-i",
        "inventory/deploy-localhost.ini",
        str(ANSIBLE_PLAYBOOK),
        "--become",
        "--extra-vars",
        f"ansible_remote_tmp={ANSIBLE_REMOTE_TMP}",
    ]
    env = git_env()
    if VAULT_PASSWORD_FILE:
        env["ANSIBLE_VAULT_PASSWORD_FILE"] = VAULT_PASSWORD_FILE
    run(command, cwd=REPO, env=env, capture=False)


def inspect_container():
    result = run(["/usr/bin/docker", "compose", "ps", "--all", "-q", COMPOSE_SERVICE], cwd=APP_DIRECTORY, check=False)
    container_id = (result.stdout or "").strip()
    if result.returncode or not container_id:
        return None
    result = run(["/usr/bin/docker", "inspect", "--format", "{{.Config.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.Image}}", container_id], check=False)
    if result.returncode:
        return None
    parts = result.stdout.strip().split("|", 3)
    if len(parts) != 4:
        return None
    return {"image": parts[0], "status": parts[1], "health": parts[2], "image_id": parts[3]}


def wait_healthy(expected_ref):
    deadline = time.monotonic() + HEALTH_TIMEOUT
    while True:
        container = inspect_container()
        if container:
            if container["status"] in ("exited", "dead", "removing"):
                raise OperationalError(f"container entered state {container['status']}")
            if container["image"] == expected_ref and container["status"] == "running" and container["health"] == "healthy":
                return
        if time.monotonic() >= deadline:
            raise OperationalError(f"container did not become healthy with the expected image within {HEALTH_TIMEOUT} seconds")
        time.sleep(5)


def current_container_ref(container=None):
    container = container or inspect_container()
    if not container or container["status"] != "running":
        raise OperationalError("cannot identify a running image to preserve for rollback")
    ref = container["image"]
    if not ref.startswith(f"ghcr.io/{IMAGE_NAME}:") and not ref.startswith(f"ghcr.io/{IMAGE_NAME}@"):
        raise OperationalError("running image does not match the configured GHCR repository")
    return ref


def current_container_digest(container, preferred_digest=""):
    """Return a GHCR digest attached to the image actually used by the container."""
    if not container or not container.get("image_id"):
        raise OperationalError("cannot identify the running container image")
    result = run(
        ["/usr/bin/docker", "image", "inspect", "--format", "{{json .RepoDigests}}", container["image_id"]],
        check=False,
    )
    if result.returncode:
        raise OperationalError("cannot inspect the running container image digests")
    try:
        repo_digests = json.loads(result.stdout.strip() or "null") or []
    except json.JSONDecodeError as exc:
        raise OperationalError("Docker returned invalid image digest metadata") from exc

    prefix = f"ghcr.io/{IMAGE_NAME}@"
    digests = {
        ref[len(prefix):]
        for ref in repo_digests
        if isinstance(ref, str) and ref.startswith(prefix) and DIGEST_RE.fullmatch(ref[len(prefix):])
    }
    if preferred_digest in digests:
        return preferred_digest
    if len(digests) == 1:
        return next(iter(digests))
    if not digests:
        raise OperationalError("running container image has no matching GHCR repo digest")
    raise OperationalError("running container image has multiple GHCR digests and none matches the recorded deployed digest")


def git_sync(state):
    pending = state.get("pending_git_sync")
    if not pending:
        return True
    expected_ref = pending["image_ref"]
    if parse_image_variable() != expected_ref:
        raise OperationalError("pending Git sync conflicts with the tracked image variable; manual reconciliation is required")
    status = repo_status()
    image_path = str(VARIABLE_FILE.relative_to(GIT_CHECKOUT))
    changed_paths = [line[3:] for line in status]
    if any(path != image_path for path in changed_paths):
        raise OperationalError("pending Git sync found unrelated local changes; refusing to commit or push")
    branch = run(["git", "branch", "--show-current"], cwd=GIT_CHECKOUT).stdout.strip()
    if branch != BRANCH:
        raise OperationalError(f"pending Git sync checkout is on branch {branch!r}, expected {BRANCH!r}")
    if status:
        unstaged = run(["git", "diff", "--numstat", "--", image_path], cwd=GIT_CHECKOUT).stdout.splitlines()
        staged = run(["git", "diff", "--cached", "--numstat", "--", image_path], cwd=GIT_CHECKOUT).stdout.splitlines()
        additions = removals = 0
        for line in unstaged + staged:
            added, removed, _path = line.split("\t", 2)
            additions += int(added)
            removals += int(removed)
        if additions != 1 or removals != 1:
            raise OperationalError("pending Git sync contains edits beyond the deployment image variable; refusing to commit")
        run(["git", "add", "--", image_path], cwd=GIT_CHECKOUT)
        short = pending["digest"].split(":", 1)[-1][:12]
        run(["git", "-c", "user.name=Personal Project Deployment", "-c", "user.email=personal-project-deploy@localhost", "commit", "-m", f"Deploy {DEPLOY_NAME} {short}"], cwd=GIT_CHECKOUT)
    env = git_env()
    push = run(["git", "push", GIT_REMOTE, BRANCH], cwd=GIT_CHECKOUT, check=False, env=env)
    if push.returncode:
        log("Git push was rejected or unavailable; attempting a safe rebase of the deployment commit")
        run(["git", "fetch", GIT_REMOTE, BRANCH], cwd=GIT_CHECKOUT, env=env)
        local_paths = run(["git", "diff", "--name-only", f"{GIT_REMOTE}/{BRANCH}...HEAD"], cwd=GIT_CHECKOUT).stdout.splitlines()
        if any(path != image_path for path in local_paths):
            raise OperationalError("Git push is rejected and local commits include unrelated files; sync remains pending")
        rebase = run(["git", "rebase", f"{GIT_REMOTE}/{BRANCH}"], cwd=GIT_CHECKOUT, check=False, env=env)
        if rebase.returncode:
            run(["git", "rebase", "--abort"], cwd=GIT_CHECKOUT, check=False, env=env)
            raise OperationalError("Git sync rebase conflicted; deployed image remains healthy and sync is pending")
        if parse_image_variable() != expected_ref:
            raise OperationalError("remote changed the deployment image during sync; manual reconciliation is required")
        run(["git", "push", GIT_REMOTE, BRANCH], cwd=GIT_CHECKOUT, env=env)
    state["pending_git_sync"] = None
    save_state(state)
    log(f"Git synchronization completed for {pending['digest']}")
    return True


def pull_latest_code():
    ensure_clean_repository()
    branch = run(["git", "branch", "--show-current"], cwd=GIT_CHECKOUT).stdout.strip()
    if branch != BRANCH:
        raise OperationalError(f"infrastructure checkout is on branch {branch!r}, expected {BRANCH!r}")
    run(["git", "pull", "--ff-only", GIT_REMOTE, BRANCH], cwd=GIT_CHECKOUT, env=git_env())
    local_head = run(["git", "rev-parse", "HEAD"], cwd=GIT_CHECKOUT).stdout.strip()
    remote_head = run(["git", "rev-parse", f"{GIT_REMOTE}/{BRANCH}"], cwd=GIT_CHECKOUT).stdout.strip()
    if local_head != remote_head:
        raise OperationalError("infrastructure checkout contains local commits; refusing to include them in deployment sync")


def blacklist(state, digest, reason):
    failures = state.setdefault("failed_digests", [])
    if not any(item.get("digest") == digest for item in failures):
        failures.append({"digest": digest, "failed_at": int(time.time()), "reason": reason[:500]})


def finish_rollback(state, attempt, cause, rollback_error=""):
    candidate = attempt["digest"]
    previous_ref = attempt["previous_ref"]
    rollback_ok = not rollback_error
    state["successful_digest"] = attempt.get("previous_digest", state.get("successful_digest", ""))
    state["successful_ref"] = previous_ref
    state["in_progress"] = None if rollback_ok else {**attempt, "phase": "rollback_failed", "failure": cause, "rollback_error": rollback_error}
    state["pending_git_sync"] = {"digest": attempt["previous_digest"], "image_ref": previous_ref} if rollback_ok else None
    blacklist(state, candidate, cause)
    save_state(state)
    if not rollback_ok:
        raise OperationalError(f"deployment failed ({cause}); CRITICAL rollback failure ({rollback_error})")
    raise OperationalError(f"deployment failed and rollback succeeded: {cause}")


def rollback(state, attempt, cause):
    candidate = attempt["digest"]
    previous_ref = attempt["previous_ref"]
    log(f"deployment failed for {candidate}; restoring previous image")
    state["in_progress"] = {**attempt, "phase": "rolling_back", "failure": cause}
    save_state(state)
    try:
        set_image_variable(previous_ref)
        run_playbook()
        wait_healthy(previous_ref)
        log("previous application image is healthy again")
    except Exception as exc:
        rollback_error = str(exc)
        log(f"CRITICAL: rollback failed: {rollback_error}")
        finish_rollback(state, attempt, cause, rollback_error)
    finish_rollback(state, attempt, cause)


def recover_interrupted(state):
    attempt = state.get("in_progress")
    if not attempt:
        return
    if attempt.get("phase") == "rollback_failed":
        raise OperationalError("previous deployment has an unresolved rollback failure; manual recovery is required")
    if attempt.get("phase") == "rolling_back":
        container = inspect_container()
        if container and container["image"] == attempt["previous_ref"] and container["status"] == "running" and container["health"] == "healthy":
            log("recovered an interrupted rollback after verifying the previous image is healthy")
            finish_rollback(state, attempt, attempt.get("failure", "deployment failed before restart"))
    candidate_ref = attempt["image_ref"]
    container = inspect_container()
    if container and container["image"] == candidate_ref and container["status"] == "running" and container["health"] == "healthy":
        log("recovered interrupted deployment after verifying the candidate is healthy")
        state["successful_digest"] = attempt["digest"]
        state["successful_ref"] = candidate_ref
        state["pending_git_sync"] = {"digest": attempt["digest"], "image_ref": candidate_ref}
        state["in_progress"] = None
        save_state(state)
        return
    rollback(state, attempt, "interrupted deployment did not leave the candidate healthy")


def retry_failed_rollback(state):
    attempt = state.get("in_progress")
    if not attempt or attempt.get("phase") != "rollback_failed":
        raise OperationalError("there is no recorded rollback failure to retry")
    cause = attempt.get("failure", "deployment failed")
    log(f"manually retrying rollback to {attempt['previous_ref']}")
    state["in_progress"] = {**attempt, "phase": "rolling_back"}
    save_state(state)
    try:
        set_image_variable(attempt["previous_ref"])
        run_playbook()
        wait_healthy(attempt["previous_ref"])
    except Exception as exc:
        state["in_progress"] = {
            **attempt,
            "phase": "rollback_failed",
            "failure": cause,
            "rollback_error": str(exc),
        }
        save_state(state)
        raise OperationalError(f"manual rollback retry failed: {exc}") from exc

    state["successful_digest"] = attempt["previous_digest"]
    state["successful_ref"] = attempt["previous_ref"]
    state["in_progress"] = None
    state["pending_git_sync"] = {
        "digest": attempt["previous_digest"],
        "image_ref": attempt["previous_ref"],
    }
    blacklist(state, attempt["digest"], cause)
    save_state(state)
    log(f"manual rollback recovery succeeded; {attempt['previous_ref']} is healthy")


def main():
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", DEPLOY_NAME):
        raise OperationalError("configured deployment name is invalid")
    if not IMAGE_NAME_RE.fullmatch(IMAGE_NAME):
        raise OperationalError("configured GHCR image name is invalid")
    if not TAG_RE.fullmatch(WATCH_TAG):
        raise OperationalError("configured GHCR watched tag is invalid")
    if not VARIABLE_NAME_RE.fullmatch(VAR_NAME):
        raise OperationalError("configured image variable name is invalid")
    LOCK_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with LOCK_FILE.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = load_state()
        if len(sys.argv) == 4 and sys.argv[1] == "clear-failed":
            if sys.argv[2] != DEPLOY_NAME:
                raise OperationalError("manual failure-history command does not match this deployment")
            digest = sys.argv[3]
            if not DIGEST_RE.fullmatch(digest):
                raise OperationalError("manual retry requires a valid sha256 digest")
            before = len(state.get("failed_digests", []))
            state["failed_digests"] = [item for item in state.get("failed_digests", []) if item.get("digest") != digest]
            save_state(state)
            log(f"removed {digest} from failed image history ({before - len(state['failed_digests'])} entry removed)")
            return 0
        if len(sys.argv) == 3 and sys.argv[1] == "retry-rollback":
            if sys.argv[2] != DEPLOY_NAME:
                raise OperationalError("manual recovery command does not match this deployment")
            retry_failed_rollback(state)
            return 0
        if len(sys.argv) != 1:
            raise OperationalError(
                "usage: personal-project-deploy clear-failed <deployment-name> sha256:<digest> "
                "| retry-rollback <deployment-name>"
            )
        recover_interrupted(state)
        if state.get("pending_git_sync"):
            git_sync(state)

        digest = resolve_digest(WATCH_TAG)
        candidate_ref = image_ref(digest)
        try:
            configured_ref = parse_image_variable()
        except OperationalError:
            # A newly enabled watcher may be configured before the VPS
            # checkout has received its image variable. Refresh the clean
            # checkout once before treating the missing entry as an error.
            pull_latest_code()
            configured_ref = parse_image_variable()
        running_container = inspect_container()
        running_ref = current_container_ref(running_container)
        if configured_ref != running_ref:
            raise OperationalError("tracked image variable differs from the running container; refusing automatic deployment")
        preferred_running_digest = state.get("successful_digest", "")
        if not preferred_running_digest and "@" in running_ref:
            preferred_running_digest = running_ref.split("@", 1)[1]
        running_digest = current_container_digest(running_container, preferred_running_digest)
        if state.get("successful_digest"):
            if state["successful_digest"] != running_digest or state.get("successful_ref") != running_ref:
                raise OperationalError("recorded deployed state differs from the running container; refusing automatic deployment")
        else:
            state["successful_digest"] = running_digest
            state["successful_ref"] = running_ref
            save_state(state)
        if digest in {item.get("digest") for item in state.get("failed_digests", [])}:
            log(f"skipping blacklisted digest {digest}")
            return 0
        if digest == running_digest:
            log("watched image is already deployed")
            return 0

        # An image already recorded as successful but differing from Git means
        # the state, checkout, or actual deployment needs human reconciliation.
        if state.get("successful_ref") and parse_image_variable() != state["successful_ref"]:
            raise OperationalError("tracked image differs from recorded deployed image; refusing automatic deployment")

        pull_latest_code()
        configured_ref = parse_image_variable()
        current_ref = current_container_ref()
        if current_ref != state["successful_ref"] or configured_ref != state["successful_ref"]:
            raise OperationalError("deployment state changed during Git synchronization; refusing unsafe image update")
        previous_digest = state["successful_digest"]
        previous_ref = image_ref(previous_digest)

        # Pull and verify the immutable image before journaling a deployment.
        # A registry/network failure here leaves the current service and failed
        # digest history untouched, so a temporary outage does not blacklist it.
        run(["/usr/bin/docker", "pull", candidate_ref])

        attempt = {"digest": digest, "image_ref": candidate_ref, "previous_digest": previous_digest, "previous_ref": previous_ref, "previous_tracked_ref": configured_ref, "started_at": int(time.time()), "phase": "deploying"}
        state["in_progress"] = attempt
        save_state(state)
        try:
            set_image_variable(candidate_ref)
            run_playbook()
            wait_healthy(candidate_ref)
        except Exception as exc:
            rollback(state, attempt, str(exc))

        state["successful_digest"] = digest
        state["successful_ref"] = candidate_ref
        state["in_progress"] = None
        state["pending_git_sync"] = {"digest": digest, "image_ref": candidate_ref}
        save_state(state)
        log(f"deployment healthy for {digest}; synchronizing Git")
        git_sync(state)
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BlockingIOError:
        log("another deployment check is already running")
        sys.exit(0)
    except OperationalError as exc:
        log(f"ERROR: {exc}")
        sys.exit(1)
