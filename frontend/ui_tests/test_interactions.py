"""Interaction/lifecycle regressions against mounted Svelte, without native execution."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode

import pytest
from playwright.sync_api import expect
BROWSER = os.environ.get('TSP_UI_BROWSER', 'chromium')
FRONTEND = Path(__file__).resolve().parents[1]


def inspect_button(page, attempt):
    return page.get_by_role("button", name=re.compile(rf"^Inspect attempt .*{re.escape(attempt)}"))


def test_failed_rerun_invalidates_previous_review(ui):
    page, api = ui
    api.connect(); api.prepare_review(); api.select_job()
    api.fail_next["/jobs/review"] = "job_stale"
    page.get_by_role("button", name="Review fresh rerun").click()
    expect(page.get_by_text("Injected job_stale", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Run reviewed experiment")).to_have_count(0)
    assert not any(path == "/jobs/submit" for path, _ in api.calls)


def test_ga_job_rerun_never_authorizes_an_unrepresentable_review(ui):
    page, api = ui
    api.connect(); api.prepare_review(); api.select_job()
    api.accepted_review['plan']['configuration'].update(algorithm='ga',
        implementations=['serial-generational-v1'], budget={'max_generations': 3},
        logical_search_ids=['population-1'])
    before = len(api.calls)
    page.get_by_role('button', name='Review fresh rerun').click()
    expect(page.get_by_text('GA rerun is not supported in this editor.', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Run reviewed experiment')).to_have_count(0)
    assert [path for path, _ in api.calls[before:]] == ['/jobs/review']


def test_unrelated_error_preserves_analysis_and_verified_result(ui):
    page, api = ui
    api.connect(); api.prepare_review(); api.select_job()
    inspect_button(page, api.attempt).click()
    expect(page.get_by_role("heading", name="Verified final result")).to_be_visible()
    page.get_by_role('combobox', name='Analysis table', exact=True).select_option("progress")
    page.get_by_role("button", name="Show analysis", exact=True).click()
    expect(page.get_by_role("img", name="Recorded cost observations on this page, without interpolation")).to_be_visible()
    api.fail_next["/instances/load"] = "invalid_artifact"
    page.get_by_label("Or load saved instance").select_option(api.candidates()[0]["artifact_handle"])
    page.get_by_role("button", name="Load instance", exact=True).click()
    expect(page.get_by_text("Injected invalid_artifact", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Verified final result")).to_be_visible()
    expect(page.get_by_role('combobox', name='Analysis table', exact=True)).to_have_value("progress")
    page.get_by_role("button", name="Refresh jobs", exact=True).click()
    expect(page.get_by_role('combobox', name='Analysis table', exact=True)).to_have_value("progress")
    expect(page.get_by_role("heading", name="Verified final result")).to_be_visible()


def test_shared_catalog_and_reconnect_reset_scoped_selections(ui):
    page, api = ui
    api.connect()
    page.get_by_role("link", name="Saved results", exact=True).click()
    page.get_by_label("runs.jsonl", exact=False).check()
    expect(page.get_by_role("button", name="Load 1 selected shard(s)", exact=True)).to_be_enabled()
    page.get_by_role("link", name="Experiments", exact=True).click()
    page.get_by_role("button", name="Refresh files", exact=True).click()
    expect(page.get_by_label("Or load saved instance")).to_be_enabled()
    page.get_by_label("Or load saved instance").select_option(api.candidates()[0]["artifact_handle"])
    page.get_by_role("button", name="Load instance", exact=True).click()
    expect(page.get_by_text("Instance ready: 4 cities", exact=False)).to_be_visible()
    page.get_by_role("link", name="Saved results", exact=True).click()
    expect(page.get_by_label("runs.jsonl", exact=False)).not_to_be_checked()
    expect(page.get_by_role("button", name="Load 0 selected shard(s)", exact=True)).to_be_disabled()
    page.get_by_label("runs.jsonl", exact=False).check()
    page.get_by_role("link", name="Experiments", exact=True).click()
    page.get_by_role("button", name="Disconnect", exact=True).click()
    api.connect()
    page.get_by_role("link", name="Saved results", exact=True).click()
    expect(page.get_by_label("runs.jsonl", exact=False)).not_to_be_checked()
    expect(page.get_by_role("button", name="Load 0 selected shard(s)", exact=True)).to_be_disabled()
    page.get_by_role("button", name="Refresh catalog", exact=True).click()
    expect(page.get_by_role("button", name="Refresh catalog", exact=True)).to_be_enabled()
    page.get_by_role("link", name="Experiments", exact=True).click()
    page.get_by_label("Or load saved instance").select_option(api.candidates()[0]["artifact_handle"])
    page.get_by_role("button", name="Load instance", exact=True).click()
    expect(page.get_by_text("Instance ready: 4 cities", exact=False)).to_be_visible()


def test_configuration_draft_restores_without_reusing_review_authority(ui):
    page, api = ui
    api.connect(); api.prepare_review()
    page.get_by_label('Repetitions', exact=True).fill('3')
    page.reload(); api.connect()
    expect(page.get_by_label('Repetitions', exact=True)).to_have_value('3')
    expect(page.get_by_role('button', name='Run reviewed experiment', exact=True)).to_have_count(0)
    page.get_by_role('button', name='Restore saved input snapshot', exact=True).click()
    expect(page.get_by_text('Instance ready: 4 cities', exact=False)).to_be_visible()
    expect(page.get_by_role('button', name='Review experiment', exact=True)).to_be_enabled()
    assert not any(path == '/jobs/submit' for path, _ in api.calls)
    stored = page.evaluate('JSON.stringify(Object.entries(sessionStorage))')
    assert 'fixture-token' not in stored and 'preflight_id' not in stored


def test_multicore_resources_round_trip_without_restoring_authority(ui):
    page, api = ui
    api.capabilities['limits']['max_solver_threads'] = 2
    api.capabilities['implementations'].append({**api.capabilities['implementations'][0],
        'implementation': 'visited-scan-threaded-v1', 'backend': 'multicore',
        'default': False, 'supports_time': False, 'supports_history': False})
    api.connect(); api.prepare_review()
    backend = page.get_by_role('combobox', name='Execution backend', exact=True)
    backend.select_option('multicore')
    expect(page.get_by_role('button', name='Run reviewed experiment', exact=True)).to_have_count(0)
    page.get_by_label('Solver threads', exact=True).fill('2')
    expect(page.get_by_label('Record best-so-far history', exact=True)).to_be_disabled()
    page.get_by_role('button', name='Review experiment', exact=True).click()
    expect(page.get_by_role('button', name='Run reviewed experiment', exact=True)).to_be_enabled()
    config = next(body['configuration'] for path, body in reversed(api.calls) if path == '/runs/preflight')
    assert config['backend'] == 'multicore' and config['threads_per_worker'] == 2
    assert config['implementations'] == ['visited-scan-threaded-v1']
    assert config['record_history'] is False and 'time_limit_s' not in config['budget']
    page.reload(); api.connect()
    expect(backend).to_have_value('multicore')
    expect(page.get_by_label('Solver threads', exact=True)).to_have_value('2')
    expect(page.get_by_role('button', name='Run reviewed experiment', exact=True)).to_have_count(0)
    page.get_by_role('button', name='Restore saved input snapshot', exact=True).click()
    expect(page.get_by_text('Instance ready: 4 cities', exact=False)).to_be_visible()
    backend.select_option('serial')
    expect(page.get_by_label('Solver threads', exact=True)).to_have_value('1')
    assert not any(path == '/jobs/submit' for path, _ in api.calls)


def test_review_expiration_is_visible_and_disables_admission(ui):
    page, api = ui
    page.clock.install()
    api.connect(); api.prepare_review()
    before = len(api.calls)
    page.clock.fast_forward((api.preflight['valid_for_s'] + 1) * 1000)
    expect(page.get_by_text('Review expired.', exact=False)).to_be_visible()
    expect(page.get_by_role('button', name='Run reviewed experiment', exact=True)).to_be_disabled()
    assert len(api.calls) == before


def test_analysis_completion_does_not_steal_navigation(ui):
    page, api = ui
    api.connect(); api.select_job()
    api.hold('/jobs/analysis')
    page.get_by_role('combobox', name='Analysis table', exact=True).select_option('summary')
    page.get_by_role('button', name='Show analysis', exact=True).click()
    expect(page.get_by_role('button', name='Show analysis', exact=True)).to_be_disabled()
    page.get_by_role('link', name='Saved results', exact=True).click()
    api.release('/jobs/analysis')
    expect(page.get_by_role('heading', name='Local experiment results')).to_be_visible()
    assert page.url.endswith('#saved')
    count = sum(path == '/jobs/analysis' for path, _ in api.calls)
    page.get_by_role('link', name='Experiments', exact=True).click()
    expect(page.get_by_role('combobox', name='Analysis table', exact=True)).to_have_value('summary')
    expect(page.get_by_role('button', name='Show analysis', exact=True)).to_be_enabled()
    assert sum(path == '/jobs/analysis' for path, _ in api.calls) == count


def test_return_to_pending_query_and_inspection_does_not_replay_or_cancel_them(ui):
    page, api = ui
    api.connect(); api.select_job()
    api.hold('/jobs/result'); api.hold('/jobs/analysis')
    inspect_button(page, api.attempt).click()
    expect(page.locator('.result-slot')).to_have_attribute('aria-busy', 'true')
    page.get_by_role('combobox', name='Analysis table', exact=True).select_option('summary')
    page.get_by_role('button', name='Show analysis', exact=True).click()
    expect(page.get_by_role('button', name='Show analysis', exact=True)).to_be_disabled()
    page.get_by_role('link', name='Saved results', exact=True).click()
    counts = {name: sum(path == name for path, _ in api.calls) for name in ('/jobs/analysis', '/jobs/result')}
    page.get_by_role('link', name='Experiments', exact=True).click()
    expect(page.get_by_role('combobox', name='Analysis table', exact=True)).to_have_value('summary')
    api.release('/jobs/analysis'); api.release('/jobs/result')
    expect(page.get_by_role('button', name='Show analysis', exact=True)).to_be_enabled()
    expect(page.get_by_role('heading', name='Verified final result')).to_be_visible()
    assert all(sum(path == name for path, _ in api.calls) == count for name, count in counts.items())


def test_unavailable_saved_implementation_does_not_trap_configuration(ui):
    page, api = ui
    api.connect(); api.prepare_review()
    page.evaluate("""() => {
      const key = Object.keys(sessionStorage).find(key => key.startsWith('tsp-hpc-configuration-draft-v1:'));
      const draft = JSON.parse(sessionStorage.getItem(key));
      draft.implementations = ['removed-implementation'];
      sessionStorage.setItem(key, JSON.stringify(draft));
    }""")
    page.reload(); api.connect()
    expect(page.get_by_text('Unavailable saved implementations were deselected.', exact=False)).to_be_visible()
    page.get_by_role('button', name='Restore saved input snapshot', exact=True).click()
    page.get_by_label('visited-scan-v1', exact=True).check()
    page.get_by_role('button', name='Review experiment', exact=True).click()
    expect(page.get_by_role('button', name='Run reviewed experiment', exact=True)).to_be_enabled()


def test_unauthorized_invalidates_both_views(ui):
    page, api = ui
    api.connect(); api.select_job()
    api.fail_next["/artifacts/refresh"] = "unauthorized"
    page.get_by_role("button", name="Refresh files", exact=True).click()
    expect(page.get_by_role("button", name="Connect", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Disconnect", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Generate instance", exact=True)).to_have_count(0)
    page.get_by_role("link", name="Saved results", exact=True).click()
    expect(page.get_by_role("link", name="Connect in Experiments", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Load 0 selected shard(s)", exact=True)).to_have_count(0)


def test_invalid_retry_metadata_has_explicit_owner_recovery(ui):
    page, api = ui
    page.evaluate("sessionStorage.setItem('tsp-hpc-pending-submission-v1', '{broken')")
    page.reload(); api.connect()
    expect(page.get_by_role("heading", name="Saved retry metadata is unreadable")).to_be_visible()
    expect(page.get_by_role("button", name="Generate instance", exact=True)).to_be_disabled()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Clear retry reference after inspection").click()
    expect(page.get_by_role("button", name="Generate instance", exact=True)).to_be_enabled()
    assert page.evaluate("sessionStorage.getItem('tsp-hpc-pending-submission-v1')") is None
    assert not any(path == "/jobs/submit" for path, _ in api.calls)


def test_url_navigation_repairing_and_wrong_root(ui):
    page, api = ui
    api.connect(); api.select_job()
    inspect_button(page, api.attempt).focus()
    page.keyboard.press("Enter")
    heading = page.get_by_role("heading", name="Verified final result")
    expect(heading).to_be_focused()
    assert "attempt=" in page.url and "job=" in page.url and "root=" in page.url
    page.go_back()
    expect(heading).to_have_count(0)
    page.go_forward()
    expect(heading).to_be_visible()
    page.reload()
    expect(page.get_by_role("button", name="Connect", exact=True)).to_be_visible()
    api.connect()
    expect(heading).to_be_visible()
    assert not any(path == "/jobs/submit" for path, _ in api.calls)
    page.evaluate("hash => location.hash = hash", "#experiments?" + urlencode({"root": "wrong-root", "job": api.job["job_id"]}))
    expect(page.get_by_text("This link belongs to another output root.", exact=False)).to_be_visible()
    expect(heading).to_have_count(0)
    expect(page.get_by_role("region", name="Experiment analysis", exact=True)).to_have_count(0)


def test_query_draft_and_pagination_are_independent(ui):
    page, api = ui
    api.page_size = 1
    api.connect(); api.select_job()
    page.get_by_role('combobox', name='Analysis table', exact=True).select_option("summary")
    expect(page.get_by_text("Draft changes are not applied yet.", exact=False)).to_be_visible()
    page.get_by_role("button", name="Next analysis page", exact=True).click()
    expect(page.get_by_text("Page 2 · 1 rows", exact=True)).to_be_visible()
    assert [body["query"]["table"] for path, body in api.calls if path == "/jobs/analysis"][-1] == "trials"
    page.get_by_role("button", name="Previous analysis page", exact=True).click()
    expect(page.get_by_text("Page 1 · 1 rows", exact=True)).to_be_visible()
    page.get_by_role("button", name="Show analysis", exact=True).click()
    page.get_by_label("All columns", exact=True).check()
    cell = page.locator('td[title^="search_time_s_count:"]').first
    expect(cell).to_be_visible()
    assert not cell.get_attribute("title").endswith(" s")
    columns = page.locator('[aria-label="Experiment analysis"] th').count()
    page.get_by_label("Full-precision values", exact=True).check()
    assert page.locator('[aria-label="Experiment analysis"] th').count() == columns
    assert not cell.get_attribute("title").endswith(" s")


def test_status_read_does_not_block_stop_intent(ui):
    page, api = ui
    api.job.update(state="running", revision="running-revision", result_revision=None, result_handle=None)
    api.connect()
    page.get_by_role("button", name=re.compile(rf"^Select job .*{api.job['job_id']}")).click()
    expect(page.get_by_role("button", name="Stop after trial", exact=True)).to_be_visible()
    api.hold("/jobs/query")
    page.get_by_role("button", name="Refresh jobs", exact=True).click()
    expect(page.get_by_role("button", name="Refresh jobs", exact=True)).to_be_disabled()
    page.get_by_role("button", name="Stop after trial", exact=True).click()
    expect(page.get_by_text("Updating job…", exact=True)).to_be_visible()
    assert any(path == "/jobs/control" for path, _ in api.calls)
    api.release("/jobs/query")


@pytest.mark.skipif(BROWSER != "chromium", reason="GPU lifecycle assertions use Chromium's software WebGL; other engines run interaction/fallback coverage")
def test_renderer_identity_keyboard_and_fallback(ui):
    page, api = ui
    page.add_init_script("""(() => {
      window.viewerInitializations = 0; window.viewerTransform = null;
      const original = HTMLCanvasElement.prototype.getContext;
      HTMLCanvasElement.prototype.getContext = function(kind, ...args) {
        if (kind === 'webgl2') window.viewerInitializations++;
        return original.call(this, kind, ...args);
      };
      const locations = new WeakMap();
      const location = WebGL2RenderingContext.prototype.getUniformLocation;
      WebGL2RenderingContext.prototype.getUniformLocation = function(program, name) {
        const value = location.call(this, program, name); if (value) locations.set(value, name); return value;
      };
      const uniform = WebGL2RenderingContext.prototype.uniform4fv;
      WebGL2RenderingContext.prototype.uniform4fv = function(location, value, ...args) {
        if (locations.get(location) === 'transform') window.viewerTransform = Array.from(value);
        return uniform.call(this, location, value, ...args);
      };
    })()""")
    page.reload(); api.connect(); api.prepare_review(); api.select_job()
    inspect_button(page, api.attempt).click()
    canvas = page.get_by_label("City view.", exact=False)
    expect(page.get_by_role("button", name="Fit instance to view", exact=True)).to_be_enabled()
    canvas.focus(); page.keyboard.press("ArrowRight")
    page.wait_for_function("window.viewerTransform && window.viewerTransform[2] !== 0")
    before = page.evaluate("({count: viewerInitializations, transform: viewerTransform})")
    page.get_by_label("Repetitions", exact=True).fill("3")
    page.get_by_role("button", name="Refresh jobs", exact=True).click()
    expect(page.get_by_role("button", name="Refresh jobs", exact=True)).to_be_enabled()
    page.get_by_role("button", name="Switch to dark theme", exact=True).click()
    page.wait_for_timeout(100)
    assert page.evaluate("({count: viewerInitializations, transform: viewerTransform})") == before
    page.get_by_label("City ID", exact=True).fill("2")
    page.get_by_role("button", name="Select city", exact=True).click()
    expect(page.get_by_text("Selected city 2:", exact=False)).to_be_visible()
    page.get_by_label("Show tour", exact=True).uncheck()
    expect(page.get_by_label("Show tour", exact=True)).not_to_be_checked()
    # Keyboard alternative and explicit retry remain available if the GPU context is lost.
    canvas.evaluate("canvas => canvas.getContext('webgl2').getExtension('WEBGL_lose_context').loseContext()")
    expect(page.get_by_role("button", name="Retry viewer", exact=True)).to_be_visible()
    page.get_by_role("button", name="Retry viewer", exact=True).click()
    expect(page.get_by_role("button", name="Fit instance to view", exact=True)).to_be_enabled()


def test_late_inspection_cannot_publish_after_job_navigation(ui):
    page, api = ui
    other = deepcopy(api.job); other["job_id"] = "other-job"
    api.jobs.append(other)
    api.connect(); api.select_job()
    api.hold("/jobs/result")
    inspect_button(page, api.attempt).click()
    expect(page.locator(".result-slot")).to_have_attribute("aria-busy", "true")
    page.get_by_role("button", name="Select job other-job", exact=True).click()
    expect(page.locator('code').filter(has_text=re.compile('^other-job$'))).to_be_visible()
    api.release("/jobs/result")
    expect(page.get_by_role("heading", name="Verified final result")).to_have_count(0)


def test_accessibility_responsive_layout_theme_and_popover(ui):
    page, api = ui
    page.add_script_tag(path=str(FRONTEND / "node_modules/axe-core/axe.min.js"))
    def accessible():
        report = page.evaluate("async () => (await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}})).violations")
        assert not report, json.dumps(report, indent=2)
    accessible()
    api.connect(); api.select_job()
    inspect_button(page, api.attempt).click()
    expect(page.get_by_role("heading", name="Verified final result")).to_be_visible()
    page.get_by_text("Accessible coordinates and final permutation", exact=True).click()
    accessible()
    page.get_by_role("button", name="Switch to dark theme", exact=True).click()
    accessible()
    for width in (320, 390, 768, 1440, 2560):
        page.set_viewport_size({"width": width, "height": 1000})
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), width
    page.set_viewport_size({"width": 900, "height": 800})
    page.get_by_role("link", name="Saved results", exact=True).click()
    page.get_by_label("runs.jsonl", exact=False).check()
    page.get_by_role("button", name="Load 1 selected shard(s)", exact=True).click()
    button = page.get_by_role("button", name="Sort and filter Cities", exact=True)
    button.click()
    panel = page.get_by_role("group", name="Filter Cities", exact=True)
    expect(panel).to_be_visible()
    page.evaluate("scrollBy(0, 80)")
    page.set_viewport_size({"width": 720, "height": 700})
    expect(panel).to_be_visible()
    page.wait_for_function("""() => {
      const box = document.querySelector('.filter-panel:popover-open')?.getBoundingClientRect();
      return box && box.left >= 0 && box.right <= innerWidth && box.top >= 0 && box.bottom <= innerHeight;
    }""")
    bounds = panel.bounding_box()
    assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 720
    assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 700
    page.keyboard.press("Escape")
    expect(panel).not_to_be_visible()
    accessible()
