"""Unit tests of the forge's modules. No agent, no engine, no git."""

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from mindslice_forge import ForgeError, cli, engine, harness, lookup, pipeline, profile, split, state


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="forge-unit-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, name: str, data: str | bytes) -> Path:
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        return path


class ProfileTest(TempDirTest):
    def test_detects_encodings(self):
        self.assertEqual(profile.detect_encoding("plain ascii".encode()), "utf-8-sig")
        self.assertEqual(profile.detect_encoding("遮天".encode("utf-8")), "utf-8-sig")
        self.assertEqual(profile.detect_encoding("﻿遮天".encode("utf-8")), "utf-8-sig")
        self.assertEqual(profile.detect_encoding("遮天，叶凡".encode("gb18030")), "gb18030")
        self.assertEqual(profile.detect_encoding("遮天".encode("utf-16")), "utf-16")
        self.assertIsNone(profile.detect_encoding(b"\x89PNG\r\n\x00\x00binary"))

    def test_counts_lines_and_characters_whatever_the_newlines(self):
        for text in ("一\n二二\n三三三\n", "一\r\n二二\r\n三三三\r\n", "一\n二二\n三三三", "﻿一\n二二\n三三三\n"):
            source, = profile.scan([self.write("a.txt", text)])[0]
            self.assertEqual((source.lines, source.chars), (3, 6), repr(text))
        self.assertEqual(profile.read_lines(self.write("b.txt", "一\r\n\r\n三\r\n".encode("gb18030")), "gb18030"),
                         ["一", "", "三"])

    def test_empty_file_has_no_lines(self):
        source, = profile.scan([self.write("empty.txt", "")])[0]
        self.assertEqual((source.lines, source.chars), (0, 0))

    def test_folder_is_scanned_recursively_without_hidden_or_binary_files(self):
        self.write("docs/b.md", "b\n")
        self.write("docs/sub/a.md", "a\n")
        self.write("docs/.hidden/x.md", "x\n")
        self.write("docs/.DS_Store", "x")
        image = self.write("docs/pic.png", b"\x89PNG\x00\x00")
        sources, skipped = profile.scan([self.tmp / "docs"])
        self.assertEqual([s.path.name for s in sources], ["b.md", "a.md"])
        self.assertEqual(skipped, [image])
        self.assertIn("pic.png", profile.render(sources, skipped))

    def test_missing_source_is_an_error(self):
        with self.assertRaises(ForgeError):
            profile.scan([self.tmp / "nowhere.txt"])

    def test_same_file_named_twice_is_listed_once(self):
        path = self.write("a.txt", "a\n")
        self.assertEqual(profile.collect([path, path]), [path])


class SplitTest(TempDirTest):
    """A ten-line source of ten characters per line, and manifests that do or do not cover it."""

    def setUp(self):
        super().setUp()
        path = self.write("book.txt", "".join(f"line {n:02d}..\n" for n in range(1, 11)))
        self.sources = profile.scan([path])[0]
        self.source = str(path)
        self.lines = profile.read_lines(path, "utf-8-sig")
        self.chunks = self.tmp / "chunks"

    def manifest(self, ranges, skipped=(), texts=None):
        """Write a manifest with one chunk per (start, end) range; chunk text defaults to the source lines."""
        self.chunks.mkdir(exist_ok=True)
        chunks = []
        for index, (start, end) in enumerate(ranges):
            ident = f"{index + 1:04d}"
            text = texts[index] if texts else "\n".join(self.lines[start - 1:end])
            if text is not None:
                (self.chunks / f"{ident}.txt").write_text(text, encoding="utf-8")
            chunks.append({"id": ident, "file": f"{ident}.txt", "title": f"{start}-{end}",
                           "parts": [{"source": self.source, "start_line": start, "end_line": end}]})
        skipped = [{"source": self.source, "start_line": s, "end_line": e, "reason": "ad"} for s, e in skipped]
        (self.chunks / "manifest.json").write_text(json.dumps({"chunks": chunks, "skipped": skipped}), encoding="utf-8")

    def verify(self, chunk_size=100):
        return split.verify(self.chunks, self.sources, chunk_size)

    def test_sound_split_passes(self):
        self.manifest([(1, 5), (6, 10)])
        self.assertEqual(self.verify(), [])
        self.assertEqual([c["id"] for c in split.load(self.chunks)], ["0001", "0002"])

    def test_declared_skips_count_as_coverage(self):
        self.manifest([(2, 5), (6, 9)], skipped=[(1, 1), (10, 10)])
        self.assertEqual(self.verify(), [])

    def test_gap_is_reported(self):
        self.manifest([(1, 4), (6, 10)])
        self.assertTrue(any("lines 5-5 are in no chunk" in e for e in self.verify()))

    def test_uncovered_start_and_end_are_reported(self):
        self.manifest([(2, 9)])
        errors = self.verify()
        self.assertTrue(any("lines 1-1 are in no chunk" in e for e in errors))
        self.assertTrue(any("lines 10-10 are in no chunk" in e for e in errors))

    def test_overlap_is_reported(self):
        self.manifest([(1, 6), (5, 10)])
        self.assertTrue(any("lines 5-6 are covered more than once" in e for e in self.verify()))

    def test_range_past_the_end_is_reported(self):
        self.manifest([(1, 12)])
        self.assertTrue(any("past the last line" in e for e in self.verify()))

    def test_oversized_chunk_is_reported(self):
        self.manifest([(1, 10)])
        self.assertEqual(self.verify(chunk_size=100), [])  # 109 characters: within 1.5 x 100
        self.assertTrue(any("over the limit of 75" in e for e in self.verify(chunk_size=50)))

    def test_chunk_that_dropped_text_is_reported(self):
        self.manifest([(1, 5), (6, 10)], texts=["line 01..\n", "\n".join(self.lines[5:10])])
        self.assertTrue(any("chunk 0001" in e and "holds less text" in e for e in self.verify()))

    def test_chunk_may_reflow_whitespace_and_add_headers(self):
        self.manifest([(1, 10)], texts=["== book ==\n" + "  ".join(self.lines)])
        self.assertEqual(self.verify(chunk_size=200), [])

    def test_missing_or_unreadable_chunk_file_is_reported(self):
        self.manifest([(1, 5), (6, 10)], texts=[None, "\n".join(self.lines[5:10])])
        self.assertTrue(any("chunk 0001" in e and "cannot be read" in e for e in self.verify()))
        (self.chunks / "0001.txt").write_bytes("第一章".encode("gb18030"))
        self.assertTrue(any("chunk 0001" in e and "cannot be read as UTF-8" in e for e in self.verify()))

    def test_unknown_source_and_bad_ids_are_reported(self):
        self.manifest([(1, 5), (6, 10)])
        manifest = json.loads((self.chunks / "manifest.json").read_text(encoding="utf-8"))
        manifest["chunks"][1]["id"] = "0001"
        manifest["skipped"] = [{"source": str(self.tmp / "other.txt"), "start_line": 1, "end_line": 2, "reason": "?"}]
        (self.chunks / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        errors = self.verify()
        self.assertTrue(any("ids must be unique" in e for e in errors))
        self.assertTrue(any("other.txt is not one of the sources" in e for e in errors))

    def test_broken_manifest_is_one_clear_error(self):
        self.assertIn("missing or does not follow the contract", self.verify()[0])
        self.chunks.mkdir()
        for content in ("not json", '{"chunks": [{"id": "0001"}]}', '{"parts": []}', '{"chunks": []}'):
            (self.chunks / "manifest.json").write_text(content, encoding="utf-8")
            self.assertEqual(len(self.verify()), 1, content)

    def test_single_chunk_holds_every_source_and_verifies(self):
        other = self.write("notes/extra.md", "附录\n".encode("gb18030"))
        sources = profile.scan([Path(self.source), other])[0]
        split.single_chunk(self.chunks, sources)
        self.assertEqual(split.verify(self.chunks, sources, 1000), [])
        text = (self.chunks / "0001.txt").read_text(encoding="utf-8")
        self.assertIn("line 10..", text)
        self.assertIn("附录", text)
        self.assertIn(f"===== {other} =====", text)

    def test_run_splitter_reports_failures_and_clears_old_chunks(self):
        self.manifest([(1, 10)])
        script = self.write("split.py", "import sys; sys.stderr.write('boom'); sys.exit(3)")
        failure = split.run_splitter(script, self.chunks, self.sources, 100)
        self.assertIn("exited with status 3", failure)
        self.assertIn("boom", failure)
        self.assertFalse(self.chunks.exists())
        self.assertIn("does not exist", split.run_splitter(self.tmp / "none.py", self.chunks, self.sources, 100))

    def test_run_splitter_passes_the_contract_arguments(self):
        script = self.write("split.py", "import sys, pathlib; pathlib.Path('argv.txt').write_text('|'.join(sys.argv[1:]))")
        self.assertIsNone(split.run_splitter(script, self.chunks, self.sources, 123))
        self.assertEqual((self.tmp / "argv.txt").read_text(), f"--chunk-size|123|--out|{self.chunks}|{self.source}")


class StateTest(TempDirTest):
    def test_fingerprint_follows_contents_prompt_and_chunk_size(self):
        a, b = self.write("a.txt", "one"), self.write("b.txt", "two")
        base = state.fingerprint([a, b], "prompt", 100)
        self.assertEqual(base, state.fingerprint([a, b], "prompt", 100))
        self.assertNotEqual(base, state.fingerprint([a, b], "other prompt", 100))
        self.assertNotEqual(base, state.fingerprint([a, b], "prompt", 200))
        self.assertNotEqual(base, state.fingerprint([a], "prompt", 100))
        b.write_text("changed")
        self.assertNotEqual(base, state.fingerprint([a, b], "prompt", 100))

    def test_save_and_load(self):
        path = self.tmp / "state.json"
        self.assertIsNone(state.load(path))
        data = state.new("abc")
        data["chunks_done"].append("0001")
        state.save(path, data)
        self.assertEqual(state.load(path), data)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["state.json"])

    def test_rewind_forgets_the_stage_and_everything_after_it(self):
        done = {"fingerprint": "x", "plan": True, "split": True, "chunks_done": ["0001"], "finalized": True}
        expected = {
            "plan": {"plan": False, "split": False, "chunks_done": [], "finalized": False},
            "split": {"plan": True, "split": False, "chunks_done": [], "finalized": False},
            "extract": {"plan": True, "split": True, "chunks_done": [], "finalized": False},
            "finalize": {"plan": True, "split": True, "chunks_done": ["0001"], "finalized": False},
        }
        for stage, after in expected.items():
            data = json.loads(json.dumps(done))
            state.rewind(data, stage)
            self.assertEqual(data, {"fingerprint": "x", **after}, stage)


class EngineIssuesTest(unittest.TestCase):
    REPORT = {
        "errors": [{"severity": "error", "code": "broken_lineage", "message": "m", "file": "/ws/slices/a.slice/x.md"}],
        "warnings": [
            {"severity": "warning", "code": "card_too_long", "message": "m", "file": "/ws/slices/a.slice/new.md"},
            {"severity": "warning", "code": "card_too_long", "message": "m", "file": "/ws/slices/a.slice/old.md"},
            {"severity": "warning", "code": "yaml_frontmatter", "message": "m", "file": "/ws/slices/a.slice/new.md"},
            {"severity": "warning", "code": "duplicate_alias", "message": "m", "address": "@a"},
        ],
    }

    def codes(self, issues):
        return [(i["code"], Path(i.get("file", "")).name) for i in issues]

    def test_errors_always_and_fixable_warnings_on_the_given_files(self):
        issues = engine.issues(self.REPORT, {Path("/ws/slices/a.slice/new.md")})
        self.assertEqual(self.codes(issues), [("broken_lineage", "x.md"), ("card_too_long", "new.md")])

    def test_without_files_every_fixable_warning_counts(self):
        issues = engine.issues(self.REPORT)
        self.assertEqual(self.codes(issues),
                         [("broken_lineage", "x.md"), ("card_too_long", "new.md"), ("card_too_long", "old.md")])

    def test_clean_report_has_no_issues(self):
        self.assertEqual(engine.issues({"errors": [], "warnings": []}, set()), [])


class LookupTest(TempDirTest):
    def test_a_lookup_covers_a_card_it_names(self):
        names = ["叶凡（灵墟洞天初修期）", "圣体叶凡"]
        for query in ("叶凡", "叶凡 灵墟洞天", "圣体叶凡是谁", "灵墟洞天初修期"):
            self.assertTrue(lookup.covered(names, [query]), query)
        for query in ("庞博", "荒古禁地 红果", ""):
            self.assertFalse(lookup.covered(names, [query]), query)

    def test_coverage_ignores_case_and_needs_some_lookup(self):
        self.assertTrue(lookup.covered(["Deploy Rollback"], ["unrelated", "ROLLBACK procedure"]))
        self.assertTrue(lookup.covered(["API keys"], ["where are the api keys stored"]))
        self.assertFalse(lookup.covered(["Deploy Rollback"], []))
        self.assertFalse(lookup.covered([], ["anything"]))

    def test_queries_are_read_from_the_log_next_to_the_workspace(self):
        workspace = self.tmp / "ws"
        self.assertEqual(lookup.log_file(workspace), self.tmp / "ws.forge" / "lookups.jsonl")
        self.assertEqual(lookup.queries(workspace), [])
        self.write("ws.forge/lookups.jsonl", '{"time": 1, "query": "叶凡"}\n\n{"time": 2, "query": "rollback"}\n')
        self.assertEqual(lookup.queries(workspace), ["叶凡", "rollback"])

    def test_script_needs_a_workspace_and_a_name(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(lookup.main([str(self.tmp)]), 2)
        self.assertIn("lookup.py WORKSPACE NAME", err.getvalue())


class CardNamesTest(TempDirTest):
    def test_title_and_aliases(self):
        card = self.write("a.md", '+++\ntitle = "叶凡"\naliases = ["主角", "圣体"]\ntags = ["x"]\n+++\n\n叶凡。\n')
        self.assertEqual(engine.card_names(card), ["叶凡", "主角", "圣体"])

    def test_file_name_when_there_is_no_usable_frontmatter(self):
        for name, text in (("plain-note.md", "# Heading\n\ntext\n"), ("broken.md", '+++\ntitle = "unterminated\n+++\n'),
                           ("empty.md", '+++\naliases = "not a list"\n+++\n\ntext\n')):
            self.assertEqual(engine.card_names(self.write(name, text)), [Path(name).stem], name)


class HarnessTest(TempDirTest):
    def test_preset_template_gets_model_and_directories(self):
        cmd = harness.template("agy", None, "some-model", [Path("/a"), Path("/b")])
        self.assertEqual(cmd[:2], ["agy", "--print={message}"])
        self.assertEqual(cmd[-6:], ["--model", "some-model", "--add-dir", "/a", "--add-dir", "/b"])
        self.assertNotIn("--model", harness.template("claude", None, None, []))

    def test_custom_template_is_used_as_given(self):
        cmd = harness.template("agy", "my-agent --flag 'two words' {prompt_file}", "ignored", [Path("/a")])
        self.assertEqual(cmd, ["my-agent", "--flag", "two words", "{prompt_file}"])

    def test_command_fills_placeholders_and_leaves_other_braces_alone(self):
        prompt = Path("/work/prompts/plan.md")
        cmd = harness.command(["x", "--print={message}", "{prompt_file}", "--cwd={workdir}", '{"json": 1}'],
                              prompt, Path("/work"))
        self.assertEqual(cmd[1], "--print=" + harness.MESSAGE.format(prompt_file=prompt))
        self.assertEqual(cmd[2:], ["/work/prompts/plan.md", "--cwd=/work", '{"json": 1}'])

    def test_run_reports_success_failure_and_timeout_and_keeps_a_log(self):
        log = self.tmp / "logs" / "step.log"
        python = [sys.executable, "-c"]
        self.assertTrue(harness.run([*python, "print('hello from the agent')"], self.tmp, log, 30))
        self.assertFalse(harness.run([*python, "import sys; sys.exit(2)"], self.tmp, log, 30))
        self.assertFalse(harness.run([*python, "import time; time.sleep(30)"], self.tmp, log, 1))
        text = log.read_text(encoding="utf-8")
        self.assertIn("hello from the agent", text)
        self.assertIn("exit status 2", text)
        self.assertIn("timed out after 1s", text)

    def test_run_uses_the_working_directory_and_no_stdin(self):
        code = "import sys, pathlib; pathlib.Path('here.txt').write_text(repr(sys.stdin.read()))"
        self.assertTrue(harness.run([sys.executable, "-c", code], self.tmp, self.tmp / "log", 30))
        self.assertEqual((self.tmp / "here.txt").read_text(), "''")

    def test_missing_command_is_a_forge_error(self):
        with self.assertRaises(ForgeError):
            harness.run(["no-such-harness-command"], self.tmp, self.tmp / "log", 5)


class PipelineHelpersTest(TempDirTest):
    VALUES = {
        "plan": ["profile", "dicts", "user_prompt", "plan_file", "slices_dir", "split_note", "split_file", "chunk_size"],
        "extract": ["plan_file", "user_prompt", "dicts", "chunk_file", "chunk_title", "chunk_parts", "lookup", "position", "out",
                    "slices_dir", "previous_handoff", "handoff_file", "ledger_file"],
        "finalize": ["plan_file", "user_prompt", "out", "slices_dir", "ledger_dir", "report_file", "coverage", "issues",
                     "profile_file", "manifest_file"],
        "repair": ["task_file", "issues"],
    }

    def test_every_template_renders_with_exactly_the_values_the_pipeline_passes(self):
        for name, keys in self.VALUES.items():
            text = pipeline.render(name, **{key: f"<<{key}>>" for key in keys})
            self.assertNotIn("{{", text, name)
            for key in keys:
                self.assertIn(f"<<{key}>>", text, f"{name} does not use {key}")
            self.assertIn("One focused sub-topic per card", text, f"{name} lacks the card format")

    def test_rendered_values_are_not_expanded_again(self):
        text = pipeline.render("repair", task_file="t", issues="a {{task_file}} in a card")
        self.assertIn("a {{task_file}} in a card", text)

    def test_missing_value_is_a_programming_error(self):
        with self.assertRaises(KeyError):
            pipeline.render("repair", task_file="t")

    def test_format_issues_is_capped(self):
        issue = {"severity": "error", "code": "c", "message": "m", "file": "/f.md"}
        self.assertEqual(pipeline.format_issues([issue]), "- error [c] /f.md: m")
        lines = pipeline.format_issues([issue] * 60).splitlines()
        self.assertEqual(len(lines), pipeline.MAX_ISSUES_SHOWN + 1)
        self.assertIn("10 more", lines[-1])
        self.assertIn("@a", pipeline.format_issues([{"severity": "warning", "code": "c", "message": "m", "address": "@a"}]))

    def test_parts_list_names_every_file_and_range_of_a_chunk(self):
        chunk = {"parts": [{"source": "/docs/a.md", "start_line": 1, "end_line": 9},
                           {"source": "/docs/b.txt", "start_line": 20, "end_line": 35}]}
        self.assertEqual(pipeline.parts_list(chunk), "- `/docs/a.md`, lines 1-9\n- `/docs/b.txt`, lines 20-35")
        self.assertEqual(pipeline.parts_list({"parts": []}), "(none)")

    def test_forge_directory_is_next_to_the_workspace(self):
        job = pipeline.Job(sources=[], dicts=[], prompt="", out=Path("/x/zhetian.workspace"), harness=[])
        self.assertEqual(job.work, Path("/x/zhetian.workspace.forge"))
        self.assertEqual(job.state_file, Path("/x/zhetian.workspace.forge/state.json"))


class CliTest(TempDirTest):
    def job(self, *argv):
        return cli.job_from(cli.parser().parse_args([str(a) for a in argv]))

    def test_prompt_is_text_or_a_file(self):
        file = self.write("prompt.md", "抽取世界观\n")
        self.assertEqual(cli.read_prompt(str(file)), "抽取世界观")
        self.assertEqual(cli.read_prompt("  抽取世界观  "), "抽取世界观")
        self.assertEqual(cli.read_prompt(""), "")
        self.assertEqual(cli.read_prompt("多行\n" + str(file)), "多行\n" + str(file))
        self.assertEqual(cli.read_prompt("长" * 5000), "长" * 5000)

    def test_defaults(self):
        source = self.write("遮天.txt", "x\n")
        job = self.job(source)
        self.assertEqual(job.out, Path.cwd() / "遮天.workspace")
        self.assertEqual((job.chunk_size, job.max_repairs, job.sample, job.from_stage), (100_000, 2, None, None))
        self.assertEqual(job.harness[0], "agy")
        self.assertEqual(job.harness[-4:], ["--add-dir", str(job.out), "--add-dir", str(self.tmp)])

    def test_default_output_keeps_a_folder_name_whole(self):
        self.write("handbook v1.2/a.md", "x\n")
        self.assertEqual(self.job(self.tmp / "handbook v1.2").out, Path.cwd() / "handbook v1.2.workspace")

    def test_dict_can_be_a_folder(self):
        source, frame = self.write("a.txt", "x\n"), self.write("ref/outline.md", "y\n")
        job = self.job(source, "--dict", self.tmp / "ref")
        self.assertEqual(job.dicts, [self.tmp / "ref"])
        self.assertIn(str(self.tmp / "ref"), job.harness)
        before = state.fingerprint([source, *profile.collect(job.dicts)], "", 1)
        frame.write_text("changed")
        self.assertNotEqual(before, state.fingerprint([source, *profile.collect(job.dicts)], "", 1))

    def test_options(self):
        source, frame = self.write("src/book.txt", "x\n"), self.write("ref/baike.txt", "y\n")
        job = self.job(self.tmp / "src", "--dict", frame, "--prompt", "p", "-o", self.tmp / "ws", "--sample", 2,
                       "--chunk-size", 500, "--harness", "claude", "--model", "m", "--from", "extract",
                       "--max-repairs", 0, "--timeout", 60, "--dry-run")
        self.assertEqual((job.sources, job.dicts, job.out), ([source.parent], [frame], self.tmp / "ws"))
        self.assertEqual((job.prompt, job.sample, job.chunk_size, job.from_stage), ("p", 2, 500, "extract"))
        self.assertEqual((job.max_repairs, job.timeout, job.dry_run), (0, 60, True))
        self.assertEqual(job.harness[0], "claude")
        self.assertIn("--model", job.harness)
        for directory in (self.tmp / "ws", self.tmp / "src", self.tmp / "ref"):
            self.assertIn(str(directory), job.harness)

    def test_custom_harness_command_replaces_the_preset(self):
        job = self.job(self.write("a.txt", "x\n"), "--harness-cmd", "my-agent {prompt_file}")
        self.assertEqual(job.harness, ["my-agent", "{prompt_file}"])

    def test_bad_arguments_exit_with_an_error(self):
        source = self.write("a.txt", "x\n")
        for argv in ([str(source), "--sample", "0"], [str(source), "--chunk-size", "0"],
                     [str(self.tmp / "missing.txt"), "--dry-run"]):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(argv), 1, argv)
            self.assertIn("error:", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
