"""Contract tests for AgentRunner plugins.

Phase 0 validation:
- Manifest structure
- Runner ID format
- Capabilities/Permissions schema
- Component loading via ComponentManifest.get_python_component_class()
- Run result schema validation
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest
import yaml
from langbot_plugin.api.definition.components.manifest import ComponentManifest
from langbot_plugin.api.entities.builtin.agent_runner import (
    AgentInput,
    AgentResources,
    AgentRunContext,
    AgentRunnerCapabilities,
    AgentRunnerPermissions,
    AgentRunResult,
    AgentRuntimeContext,
    AgentTrigger,
)

# Plugin directory paths
REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
PLUGINS = [
    "local-agent",
    "dify-agent",
    "n8n-agent",
    "coze-agent",
    "dashscope-agent",
    "langflow-agent",
    "tbox-agent",
]

# Expected runner IDs
EXPECTED_RUNNER_IDS = {
    "local-agent": "plugin:langbot/local-agent/default",
    "dify-agent": "plugin:langbot/dify-agent/default",
    "n8n-agent": "plugin:langbot/n8n-agent/default",
    "coze-agent": "plugin:langbot/coze-agent/default",
    "dashscope-agent": "plugin:langbot/dashscope-agent/default",
    "langflow-agent": "plugin:langbot/langflow-agent/default",
    "tbox-agent": "plugin:langbot/tbox-agent/default",
}


class TestManifestStructure:
    """Test that each plugin manifest has required fields."""

    @pytest.mark.parametrize("plugin_name", PLUGINS)
    def test_manifest_exists(self, plugin_name: str):
        """Each plugin must have a manifest.yaml."""
        manifest_path = REPO_ROOT / plugin_name / "manifest.yaml"
        assert manifest_path.exists(), f"{plugin_name}/manifest.yaml not found"

    @pytest.mark.parametrize("plugin_name", PLUGINS)
    def test_manifest_has_required_fields(self, plugin_name: str):
        """Each manifest must have required metadata."""
        manifest_path = REPO_ROOT / plugin_name / "manifest.yaml"
        with open(manifest_path) as f:
            manifest = yaml.safe_load(f)

        assert manifest["apiVersion"] == "langbot/v1"
        assert manifest["kind"] == "Plugin"
        assert "metadata" in manifest
        metadata = manifest["metadata"]

        # Required fields
        assert metadata["author"] == "langbot"
        assert metadata["name"] == plugin_name
        assert "label" in metadata
        assert "en_US" in metadata["label"]
        assert "zh_Hans" in metadata["label"]
        assert "description" in metadata
        assert "en_US" in metadata["description"]

        # Version
        assert "spec" in manifest
        assert "version" in manifest["spec"]

    @pytest.mark.parametrize("plugin_name", PLUGINS)
    def test_manifest_has_agent_runner_component(self, plugin_name: str):
        """Each manifest must declare AgentRunner components."""
        manifest_path = REPO_ROOT / plugin_name / "manifest.yaml"
        with open(manifest_path) as f:
            manifest = yaml.safe_load(f)

        assert "components" in manifest["spec"]
        assert "AgentRunner" in manifest["spec"]["components"]


class TestRunnerManifest:
    """Test that each AgentRunner component manifest satisfies Protocol v1."""

    @pytest.mark.parametrize("plugin_name", PLUGINS)
    def test_runner_manifest_exists(self, plugin_name: str):
        """Each plugin must have components/agent_runner/default.yaml."""
        runner_path = REPO_ROOT / plugin_name / "components" / "agent_runner" / "default.yaml"
        assert runner_path.exists(), f"{plugin_name}/components/agent_runner/default.yaml not found"

    @pytest.mark.parametrize("plugin_name", PLUGINS)
    def test_runner_manifest_has_required_fields(self, plugin_name: str):
        """Each runner manifest must have required Protocol v1 fields."""
        runner_path = REPO_ROOT / plugin_name / "components" / "agent_runner" / "default.yaml"
        with open(runner_path) as f:
            runner_manifest = yaml.safe_load(f)

        assert runner_manifest["apiVersion"] == "langbot/v1"
        assert runner_manifest["kind"] == "AgentRunner"

        # metadata
        assert "metadata" in runner_manifest
        metadata = runner_manifest["metadata"]
        assert metadata["name"] == "default"
        assert "label" in metadata
        assert "description" in metadata

        # spec
        assert "spec" in runner_manifest
        spec = runner_manifest["spec"]
        assert spec["protocol_version"] == "1"
        assert "config" in spec
        assert "capabilities" in spec
        assert "permissions" in spec

        # execution
        assert "execution" in runner_manifest
        assert "python" in runner_manifest["execution"]
        python = runner_manifest["execution"]["python"]
        # path should be relative to yaml directory (default.py)
        assert python["path"] in ("default.py", "./default.py")
        assert python["attr"] == "DefaultAgentRunner"

    @pytest.mark.parametrize("plugin_name", PLUGINS)
    def test_runner_capabilities_valid(self, plugin_name: str):
        """Capabilities must match AgentRunnerCapabilities schema."""
        runner_path = REPO_ROOT / plugin_name / "components" / "agent_runner" / "default.yaml"
        with open(runner_path) as f:
            runner_manifest = yaml.safe_load(f)

        capabilities = runner_manifest["spec"]["capabilities"]
        # Validate against Pydantic model
        caps = AgentRunnerCapabilities(**capabilities)
        assert isinstance(caps, AgentRunnerCapabilities)

    @pytest.mark.parametrize("plugin_name", PLUGINS)
    def test_runner_permissions_valid(self, plugin_name: str):
        """Permissions must match AgentRunnerPermissions schema."""
        runner_path = REPO_ROOT / plugin_name / "components" / "agent_runner" / "default.yaml"
        with open(runner_path) as f:
            runner_manifest = yaml.safe_load(f)

        permissions = runner_manifest["spec"]["permissions"]
        # Validate against Pydantic model
        perms = AgentRunnerPermissions(**permissions)
        assert isinstance(perms, AgentRunnerPermissions)


class TestRunnerId:
    """Test runner ID format matches expected."""

    @pytest.mark.parametrize("plugin_name", PLUGINS)
    def test_runner_id_format(self, plugin_name: str):
        """Runner ID must follow plugin:{author}/{name}/{runner_name} format."""
        expected_id = EXPECTED_RUNNER_IDS[plugin_name]
        parts = expected_id.split(":")
        assert parts[0] == "plugin"

        id_parts = parts[1].split("/")
        assert len(id_parts) == 3
        assert id_parts[0] == "langbot"
        assert id_parts[1] == plugin_name
        assert id_parts[2] == "default"


class TestRunnerComponentLoading:
    """Test that runners can be loaded via ComponentManifest."""

    @pytest.mark.parametrize("plugin_name", PLUGINS)
    def test_runner_can_be_loaded_via_manifest(self, plugin_name: str):
        """Each runner must be loadable via ComponentManifest.get_python_component_class()."""
        runner_yaml_path = REPO_ROOT / plugin_name / "components" / "agent_runner" / "default.yaml"

        with open(runner_yaml_path) as f:
            runner_manifest_dict = yaml.safe_load(f)

        # rel_path is relative to plugin root (where main.py is)
        # yaml is at components/agent_runner/default.yaml
        rel_path = "components/agent_runner/default.yaml"

        # Create ComponentManifest
        component_manifest = ComponentManifest(
            owner=plugin_name,
            manifest=runner_manifest_dict,
            rel_path=rel_path,
        )

        # Change to plugin directory so import works
        plugin_dir = REPO_ROOT / plugin_name
        original_cwd = os.getcwd()
        try:
            os.chdir(plugin_dir)
            # Add plugin dir to sys.path
            if str(plugin_dir) not in sys.path:
                sys.path.insert(0, str(plugin_dir))

            # Load the class
            runner_class = component_manifest.get_python_component_class()

            # Verify it's an AgentRunner subclass
            from langbot_plugin.api.definition.components.agent_runner.runner import AgentRunner

            assert issubclass(runner_class, AgentRunner)
            assert runner_class.__name__ == "DefaultAgentRunner"

        finally:
            os.chdir(original_cwd)
            # Clean up sys.path
            if str(plugin_dir) in sys.path:
                sys.path.remove(str(plugin_dir))


class TestStubRunnerExecution:
    """Test that stub runners produce valid AgentRunResult."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("plugin_name", PLUGINS)
    async def test_runner_returns_valid_result(self, plugin_name: str):
        """Each stub runner must yield valid AgentRunResult when loaded via manifest."""
        runner_yaml_path = REPO_ROOT / plugin_name / "components" / "agent_runner" / "default.yaml"

        with open(runner_yaml_path) as f:
            runner_manifest_dict = yaml.safe_load(f)

        rel_path = "components/agent_runner/default.yaml"
        component_manifest = ComponentManifest(
            owner=plugin_name,
            manifest=runner_manifest_dict,
            rel_path=rel_path,
        )

        # Change to plugin directory
        plugin_dir = REPO_ROOT / plugin_name
        original_cwd = os.getcwd()
        try:
            os.chdir(plugin_dir)
            if str(plugin_dir) not in sys.path:
                sys.path.insert(0, str(plugin_dir))

            # Load and instantiate
            runner_class = component_manifest.get_python_component_class()
            runner = runner_class()

            # Create minimal context
            ctx = AgentRunContext(
                run_id="test-run-001",
                trigger=AgentTrigger(type="message.received", source="pipeline"),
                input=AgentInput(text="Hello"),
                resources=AgentResources(),
                runtime=AgentRuntimeContext(sdk_protocol_version="1"),
                config={},
            )

            # Collect results
            results = []
            async for result in runner.run(ctx):
                results.append(result)

            # Validate each result
            for result in results:
                assert isinstance(result, AgentRunResult)

            # Must end with run.completed
            assert results[-1].type.value == "run.completed"

            # Must have at least one message event before run.completed
            message_events = [
                r for r in results if r.type.value in ("message.delta", "message.completed")
            ]
            assert len(message_events) >= 1

        finally:
            os.chdir(original_cwd)
            if str(plugin_dir) in sys.path:
                sys.path.remove(str(plugin_dir))
