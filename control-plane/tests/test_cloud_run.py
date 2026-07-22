from unittest.mock import MagicMock, patch

from rufo_control_plane.gcp.cloud_run import resolve_image_digest


def test_already_pinned_image_passes_through_unchanged():
    image = "us-central1-docker.pkg.dev/proj/repo/agent@sha256:abc123"
    assert resolve_image_digest(image) == image


def test_tagged_image_resolves_to_digest():
    tagged = "us-central1-docker.pkg.dev/rufo-cloud-06179/rufo-agents/langgraph-real-agent:latest"
    with patch("rufo_control_plane.gcp.cloud_run.artifactregistry_v1.ArtifactRegistryClient") as MockClient:
        mock_client = MockClient.return_value
        mock_client.get_tag.return_value = MagicMock(
            version="projects/rufo-cloud-06179/locations/us-central1/repositories/rufo-agents/"
            "packages/langgraph-real-agent/versions/sha256:deadbeef"
        )
        resolved = resolve_image_digest(tagged)

    assert resolved == (
        "us-central1-docker.pkg.dev/rufo-cloud-06179/rufo-agents/langgraph-real-agent@sha256:deadbeef"
    )
    called_name = mock_client.get_tag.call_args.kwargs["name"]
    assert called_name == (
        "projects/rufo-cloud-06179/locations/us-central1/repositories/rufo-agents/"
        "packages/langgraph-real-agent/tags/latest"
    )


def test_unrecognizable_image_passes_through_unchanged():
    image = "not-a-valid-reference"
    assert resolve_image_digest(image) == image
