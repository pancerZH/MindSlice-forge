"""End-to-end runs of the forge with a fake agent harness, the real ``mindslice`` command and real git.

No model is called: ``tests/fake_harness.py`` plays the agent.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mindslice_forge import ForgeError, checkpoint, cli, harness, pipeline

FAKE = f"{sys.executable} {Path(__file__).with_name('fake_harness.py')} {{prompt_file}}"

# Six two-line chapters of 90 characters each, after one advertisement line.
NOVEL = "AD: 本书来自某站\r\n" + "".join(
    f"第{i:03d}章 标题\r\n叶凡在第{i}章遇到了关键词{i}" + "。" * 70 + "\r\n" for i in range(1, 7))
NOVEL_ARGS = ["--chunk-size", "150"]  # two chapters per chunk: three chunks
CHUNK_COMMITS = ["chunk 0001: novel.txt:2-5", "chunk 0002: novel.txt:6-9", "chunk 0003: novel.txt:10-13"]


@unittest.skipUnless(shutil.which("mindslice") and shutil.which("git"), "needs the mindslice and git commands")
class FunctionalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="forge-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.out = self.tmp / "out"
        self.work = self.tmp / "out.forge"
        self.novel = self.tmp / "novel.txt"
        self.novel.write_bytes(NOVEL.encode("gb18030"))
        patch = mock.patch.object(harness, "BACKOFF", (0,))  # one immediate retry
        patch.start()
        self.addCleanup(patch.stop)

    # -- helpers ------------------------------------------------------------

    def forge(self, *args, modes="", source=None, harness_cmd=FAKE):
        """Run the CLI in-process; returns (exit code, stdout, stderr)."""
        argv = [str(source or self.novel), "-o", str(self.out), "--harness-cmd", harness_cmd, *map(str, args)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"FAKE_MODES": modes}), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def calls(self):
        log = self.work / "fake-calls.log"
        return log.read_text(encoding="utf-8").split() if log.exists() else []

    def probes(self):
        """How many times the harness command was checked."""
        log = self.work / "fake-probes.log"
        return len(log.read_text(encoding="utf-8").split()) if log.exists() else 0

    def forget_calls(self):
        (self.work / "fake-calls.log").unlink()

    def state(self):
        return json.loads((self.work / "state.json").read_text(encoding="utf-8"))

    def mindslice(self, *args, workspace=None):
        done = subprocess.run(["mindslice", "-w", str(workspace or self.out), *args, "--json"], capture_output=True,
                              text=True, encoding="utf-8")
        return json.loads(done.stdout)

    def mounted(self, workspace):
        return {m["alias"]: m["path"] for m in self.mindslice("status", workspace=workspace)["mounts"]}

    def target(self, alias="@lore"):
        """An existing workspace: a mounted slice with a card the sources also speak of and a problem of its own,
        and a recent card in its overlay."""
        target = self.tmp / "memory"
        subprocess.run(["mindslice", "init", str(target)], check=True, capture_output=True)
        lore = target / "slices" / "lore.slice"
        (lore / "people").mkdir(parents=True)
        (lore / "slice.toml").write_text(f'name = "lore"\nversion = "0.1.0"\ndescription = "Lore."\nalias = "{alias}"\n'
                                         'dependencies = []\n', encoding="utf-8")
        (lore / "people" / "hero.md").write_text('+++\ntitle = "Hero"\n+++\n\nThe hero, as known before.\n\n- Known.\n',
                                                 encoding="utf-8")
        (lore / "people" / "old.md").write_text('+++\ntitle = "Old"\nextends = ["people/nowhere"]\n+++\n\n'
                                                'A problem of the existing workspace.\n\n- Old.\n', encoding="utf-8")
        subprocess.run(["mindslice", "-w", str(target), "mount", str(lore), "--role", "baseline"], check=True,
                       capture_output=True)
        subprocess.run(["mindslice", "-w", str(target), "write", "notes/recent", "--title", "Recent", "--summary",
                        "A recent note.", "--content", "Learned lately.", "--no-stage"], check=True, capture_output=True)
        return target

    def kept(self, target):
        """The existing workspace's own memory, which a build must leave as it is."""
        return {path.relative_to(target).as_posix(): path.read_bytes() for name in ("slices/lore.slice", "overlay")
                for path in (target / name).rglob("*") if path.is_file()}

    def write_memory(self, card, *options):
        """Write a card the way an agent connected to the workspace does."""
        subprocess.run(["mindslice", "-w", str(self.out), "write", card, "--title", card, "--summary", "A note.",
                        "--content", "Learned in conversation.", *options], check=True, capture_output=True)

    def memory(self):
        """Every file of the workspace's live memory, with its content."""
        return {path.relative_to(self.out).as_posix(): path.read_bytes()
                for name in checkpoint.LIVE for path in (self.out / name).rglob("*") if path.is_file()}

    def manifest(self):
        return json.loads((self.work / "chunks" / "manifest.json").read_text(encoding="utf-8"))

    def prompt(self, step):
        return (self.work / "prompts" / f"{step}.md").read_text(encoding="utf-8")

    def folder(self):
        """A folder of sources: two small files in different encodings, one large file, a generated file,
        and two files that are not sources at all (hidden, binary)."""
        docs = self.tmp / "docs"
        (docs / "b").mkdir(parents=True)
        (docs / ".hidden").mkdir()
        (docs / "a-intro.md").write_text("荒古禁地是北斗星域的生命禁区之一。" + "。" * 23 + "\n", encoding="utf-8")
        (docs / "b" / "rules.txt").write_bytes(("轮海秘境分为苦海、命泉、神桥、彼岸四境。" + "。" * 20 + "\r\n").encode("gb18030"))
        (docs / "c-long.md").write_text(
            "".join(f"第{i}节 圣地与世家的记载" + "。" * 39 + "\n" for i in range(1, 9)), encoding="utf-8")
        (docs / "d-build.log").write_text("GENERATED by a tool\nnoise\n", encoding="utf-8")
        (docs / ".hidden" / "x.md").write_text("hidden\n", encoding="utf-8")
        (docs / "pic.png").write_bytes(b"\x89PNG\x00\x00")
        return docs

    def part_names(self, chunk):
        return [Path(part["source"]).name for part in chunk["parts"]]

    def commits(self):
        log = subprocess.run(["git", "-C", str(self.out), "log", "--format=%s", "--reverse"], capture_output=True,
                             text=True, encoding="utf-8")
        return log.stdout.splitlines()

    # -- scenarios ----------------------------------------------------------

    def test_small_document_is_one_chunk_without_a_splitter_run(self):
        source = self.tmp / "note.md"
        source.write_text("叶凡来到北斗星域，见到了九龙拉棺。\n", encoding="utf-8")
        code, _, err = self.forge(source=source)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), ["plan", "extract-0001", "finalize"])
        manifest = json.loads((self.work / "chunks" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual([c["title"] for c in manifest["chunks"]], ["all sources"])
        self.assertTrue(self.mindslice("validate")["ok"])
        hits = self.mindslice("search", "北斗星域")["results"]
        self.assertIn("@demo/story/hero-part-0001", [hit["address"] for hit in hits])

    def test_long_document_is_split_and_extracted_chunk_by_chunk(self):
        code, out, err = self.forge(*NOVEL_ARGS)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), ["plan", "extract-0001", "extract-0002", "extract-0003", "finalize"])
        self.assertEqual(self.commits(), ["empty workspace", "plan", *CHUNK_COMMITS, "finalize"])
        self.assertEqual(self.state()["chunks_done"], ["0001", "0002", "0003"])
        self.assertTrue(self.state()["finalized"])
        self.assertIn("3 of 3 chunks processed", out)
        # The GB18030 source reached the cards as text, and later chunks extend the first chunk's card.
        self.assertIn("第005章 标题", (self.out / "slices/demo.slice/story/hero-part-0003.md").read_text(encoding="utf-8"))
        composed = json.dumps(self.mindslice("read", "@demo/story/hero"), ensure_ascii=False)
        for part in ("hero-part-0001", "hero-part-0002", "hero-part-0003"):
            self.assertIn(part, composed)
        self.assertTrue(self.mindslice("validate")["ok"])
        for name in ("profile.md", "plan.md", "report.md", "ledger/0003.md", "handoff/0003.md",
                     "prompts/extract-0002.md", "logs/extract-0002.log"):
            self.assertTrue((self.work / name).is_file(), name)
        # Each session is told where the previous one left its note.
        self.assertIn(str(self.work / "handoff" / "0001.md"),
                      (self.work / "prompts" / "extract-0002.md").read_text(encoding="utf-8"))

    def test_small_folder_is_read_as_one_chunk(self):
        docs = self.folder()
        code, _, err = self.forge(source=docs)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), ["plan", "extract-0001", "finalize"])
        chunk, = self.manifest()["chunks"]
        self.assertEqual(self.part_names(chunk), ["a-intro.md", "rules.txt", "c-long.md", "d-build.log"])
        text = (self.work / "chunks" / "0001.txt").read_text(encoding="utf-8")
        for expected in ("荒古禁地", "轮海秘境", "第8节", f"===== {docs / 'b' / 'rules.txt'} ====="):
            self.assertIn(expected, text)
        self.assertNotIn("hidden", text)
        self.assertIn(f"`{docs / 'b' / 'rules.txt'}`, lines 1-1", self.prompt("extract-0001"))
        self.assertTrue(self.mindslice("validate")["ok"])

    def test_large_folder_packs_small_files_and_cuts_large_ones(self):
        docs = self.folder()
        code, out, err = self.forge("--chunk-size", "300", source=docs)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), ["plan", "extract-0001", "extract-0002", "finalize"])
        manifest = self.manifest()
        first, second = manifest["chunks"]
        self.assertEqual(self.part_names(first), ["a-intro.md", "rules.txt", "c-long.md"])  # small files share a chunk
        self.assertEqual(self.part_names(second), ["c-long.md"])                             # the large one continues
        self.assertEqual([(Path(s["source"]).name, s["reason"]) for s in manifest["skipped"]],
                         [("d-build.log", "generated file")])                                # a whole file left out
        self.assertIn("轮海秘境", (self.work / "chunks" / "0001.txt").read_text(encoding="utf-8"))
        # Each session is told which files and lines its chunk holds.
        for line in (f"`{docs / 'a-intro.md'}`, lines 1-1", f"`{docs / 'b' / 'rules.txt'}`, lines 1-1",
                     f"`{docs / 'c-long.md'}`, lines 1-5"):
            self.assertIn(line, self.prompt("extract-0001"))
        self.assertIn(f"`{docs / 'c-long.md'}`, lines 6-8", self.prompt("extract-0002"))
        self.assertNotIn("a-intro.md", self.prompt("extract-0002"))
        # The inventory names what is not text and ignores hidden files; finalize is pointed at both records.
        inventory = (self.work / "profile.md").read_text(encoding="utf-8")
        self.assertIn("pic.png", inventory)
        self.assertIn("gb18030", inventory)
        self.assertNotIn("x.md", inventory)
        self.assertIn(str(self.work / "chunks" / "manifest.json"), self.prompt("finalize"))
        self.assertIn(str(self.work / "profile.md"), self.prompt("finalize"))
        self.assertIn("2 of 2 chunks processed", out)
        self.assertTrue(self.mindslice("validate")["ok"])

    def test_files_and_folders_can_be_mixed(self):
        docs = self.folder()
        argv = [str(self.novel), str(docs), "-o", str(self.out), "--harness-cmd", FAKE, "--chunk-size", "300"]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(argv), 0, err.getvalue())
        chunks = self.manifest()["chunks"]
        self.assertEqual([self.part_names(c) for c in chunks],
                         [["novel.txt"], ["novel.txt", "a-intro.md", "rules.txt", "c-long.md"], ["c-long.md"], ["c-long.md"]])
        self.assertEqual(self.state()["chunks_done"], ["0001", "0002", "0003", "0004"])
        self.assertTrue(self.mindslice("validate")["ok"])

    def test_file_added_to_a_folder_is_noticed(self):
        docs = self.folder()
        self.assertEqual(self.forge(source=docs)[0], 0)
        self.forget_calls()
        (docs / "e-new.md").write_text("后来加入的一份资料。\n", encoding="utf-8")
        code, _, err = self.forge(source=docs)
        self.assertEqual(code, 1)
        self.assertIn("changed since this output was started", err)
        code, _, err = self.forge("--from", "plan", source=docs)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), ["plan", "extract-0001", "finalize"])
        self.assertIn("e-new.md", self.part_names(self.manifest()["chunks"][0]))

    def test_lookups_are_recorded_and_the_command_is_in_the_prompt(self):
        code, _, err = self.forge(*NOVEL_ARGS, "--sample", "2")
        self.assertEqual(code, 0, err)
        log = [json.loads(line) for line in (self.work / "lookups.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([entry["query"] for entry in log], ["Hero", "Broken", "Bare"] * 2)
        prompt = self.prompt("extract-0001")
        self.assertIn(f"lookup.py {self.out}", prompt)
        self.assertIn("The build checks this", prompt)

    def test_card_created_without_a_lookup_is_sent_back(self):
        code, _, err = self.forge(*NOVEL_ARGS, "--sample", "2", modes="no_lookup")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), ["plan", "extract-0001", "extract-0001.repair1",
                                        "extract-0002", "extract-0002.repair1", "finalize"])
        repair = self.prompt("extract-0001.repair1")
        self.assertIn("not_looked_up", repair)
        self.assertIn("story/hero.md", repair)
        self.assertIn("story/hero-part-0001.md", repair)
        # A lookup made for an earlier chunk does not excuse a card created in a later one.
        self.assertIn("story/hero-part-0002.md", self.prompt("extract-0002.repair1"))

    def test_changes_outside_the_planned_slices_are_undone_or_sent_back(self):
        code, _, err = self.forge(*NOVEL_ARGS, "--sample", "1", modes="touch_plan,engine_write")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), ["plan", "extract-0001", "extract-0001.repair1", "finalize"])
        repair = self.prompt("extract-0001.repair1")
        self.assertIn(f"{self.out / 'slices/demo.slice/slice.toml'}: a step that writes cards", repair)
        self.assertIn(f"{self.out / 'slices/extra.slice/side.md'}: cards go only into the slices of the plan", repair)
        self.assertIn(f"{self.out / 'overlay/story/side.md'}: this card was written with `mindslice write`", repair)
        self.assertNotIn("changed by the session", (self.out / "slices/demo.slice/slice.toml").read_text(encoding="utf-8"))
        self.assertEqual(checkpoint.git(self.out, "status", "--porcelain"), "")

    def test_a_commit_by_the_session_is_undone_and_its_work_still_checked(self):
        code, _, err = self.forge(*NOVEL_ARGS, "--sample", "1", modes="commit,no_lookup")
        self.assertEqual(code, 0, err)
        self.assertIn("the forge undid the commit", err + _)
        self.assertNotIn("the agent's own commit", self.commits())
        self.assertIn("story/hero.md", self.prompt("extract-0001.repair1"))  # its new cards still need lookups

    def test_card_never_looked_up_stops_the_build(self):
        code, _, err = self.forge(*NOVEL_ARGS, modes="no_lookup,never_fix")
        self.assertEqual(code, 1)
        self.assertIn("not_looked_up", err)
        self.assertIn("problems remain after 2 repair session(s)", err)
        self.assertEqual(self.state()["chunks_done"], [])

    def test_failed_attempt_lookups_do_not_cover_the_retry(self):
        code, _, err = self.forge(*NOVEL_ARGS, "--sample", "2", modes="fail_after_lookup")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), ["plan", "extract-0001", "extract-0002", "extract-0002",
                                        "extract-0002.repair1", "finalize"])
        self.assertIn("not_looked_up", self.prompt("extract-0002.repair1"))
        self.assertFalse((self.out / "slices/demo.slice/story/junk.md").exists())

    def test_cards_created_during_repair_also_need_lookups(self):
        code, _, err = self.forge("--sample", "1", modes="no_lookup,repair_new_card")
        self.assertEqual(code, 0, err)
        self.assertIn("story/repair-note.md", self.prompt("extract-0001.repair2"))
        self.assertIn("not_looked_up", self.prompt("extract-0001.repair2"))

    def test_finalization_cannot_create_cards_without_lookups(self):
        code, _, err = self.forge("--sample", "1", modes="finalize_card")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls()[-2:], ["finalize", "finalize.repair1"])
        self.assertIn("story/glossary.md", self.prompt("finalize.repair1"))
        self.assertIn("lookup.py", self.prompt("finalize"))

    def test_validation_error_triggers_a_repair_session(self):
        code, _, err = self.forge(*NOVEL_ARGS, "--sample", "1", modes="bad_card")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), ["plan", "extract-0001", "extract-0001.repair1", "finalize"])
        repair_prompt = (self.work / "prompts" / "extract-0001.repair1.md").read_text(encoding="utf-8")
        self.assertIn("broken_lineage", repair_prompt)
        self.assertIn(str(self.work / "prompts" / "extract-0001.md"), repair_prompt)
        self.assertFalse((self.out / "slices/demo.slice/story/broken.md").exists())

    def test_error_that_repairs_cannot_fix_stops_the_build_with_state_saved(self):
        code, _, err = self.forge(*NOVEL_ARGS, modes="bad_card,never_fix")
        self.assertEqual(code, 1)
        self.assertIn("problems remain after 2 repair session(s)", err)
        self.assertIn("broken_lineage", err)
        self.assertEqual(self.calls(), ["plan", "extract-0001", "extract-0001.repair1", "extract-0001.repair2"])
        self.assertTrue(self.state()["plan"])
        self.assertEqual(self.state()["chunks_done"], [])
        # Running again discards the failed attempt and continues from that chunk.
        self.forget_calls()
        code, _, err = self.forge(*NOVEL_ARGS)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), ["extract-0001", "extract-0002", "extract-0003", "finalize"])

    def test_warning_that_repairs_cannot_fix_does_not_stop_the_build(self):
        code, out, err = self.forge(*NOVEL_ARGS, "--sample", "1", modes="warn_card,never_fix")
        self.assertEqual(code, 0, err)
        self.assertIn("warning(s) left for the final report", out)
        self.assertIn("extract-0001.repair2", self.calls())

    def test_splitter_with_a_gap_is_sent_back_for_repair(self):
        code, _, err = self.forge(*NOVEL_ARGS, "--sample", "1", modes="split_gap")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls()[:3], ["plan", "split.repair1", "extract-0001"])
        self.assertIn("lines 13-13 are in no chunk", (self.work / "prompts" / "split.repair1.md").read_text(encoding="utf-8"))

    def test_failed_session_is_rolled_back_and_retried(self):
        code, _, err = self.forge(*NOVEL_ARGS, modes="fail_once")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls().count("extract-0002"), 2)
        self.assertFalse((self.out / "slices/demo.slice/junk.md").exists())
        self.assertTrue((self.out / "overlay").is_dir())

    def test_session_that_keeps_failing_stops_the_build_and_can_be_resumed(self):
        code, _, err = self.forge(*NOVEL_ARGS, modes="fail_always")
        self.assertEqual(code, 1)
        self.assertIn("extract-0001: the agent session failed 2 times", err)
        self.assertEqual(self.calls(), ["plan", "extract-0001", "extract-0001"])
        self.forget_calls()
        code, _, err = self.forge(*NOVEL_ARGS)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), ["extract-0001", "extract-0002", "extract-0003", "finalize"])
        self.assertFalse((self.out / "slices/demo.slice/junk.md").exists())

    def test_sample_run_can_be_extended_later(self):
        code, out, err = self.forge(*NOVEL_ARGS, "--sample", "2")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), ["plan", "extract-0001", "extract-0002", "finalize"])
        self.assertIn("2 of 3 chunks processed", out)
        self.assertIn("2 of 3 chunks (a partial, sample build)",
                      (self.work / "prompts" / "finalize.md").read_text(encoding="utf-8"))
        self.forget_calls()
        code, _, err = self.forge(*NOVEL_ARGS, "--sample", "3")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), ["extract-0003", "finalize"])
        self.forget_calls()
        code, _, err = self.forge(*NOVEL_ARGS)  # nothing left to do
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), [])

    def test_memory_of_a_connected_workspace_survives_resume_retry_and_redo(self):
        self.assertEqual(self.forge(*NOVEL_ARGS, "--sample", "1")[0], 0)
        # What an agent connected to the sample build leaves behind: a card, a proposal and history.
        self.write_memory("prefs/reply", "--no-stage")
        self.write_memory("tooling/python", "--stage")
        memory = self.memory()
        self.assertLessEqual({"overlay/prefs/reply.md", "staging/tooling/python.md"}, set(memory))
        self.assertTrue(any(name.startswith(".history/") for name in memory))

        code, _, err = self.forge(*NOVEL_ARGS, modes="fail_once")  # resume, with a session that is rolled back
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls().count("extract-0002"), 2)
        self.assertFalse((self.out / "slices/demo.slice/junk.md").exists())
        self.assertEqual(self.memory(), memory)

        code, _, err = self.forge(*NOVEL_ARGS, "--from", "plan")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.memory(), memory)
        self.assertEqual(checkpoint.git(self.out, "ls-files", "overlay", "staging", ".history"), "")

    def test_bake_or_mount_in_a_connected_workspace_is_never_rolled_back(self):
        self.assertEqual(self.forge(*NOVEL_ARGS, "--sample", "1")[0], 0)
        self.write_memory("story/hero-note", "--extends", "@demo/story/hero", "--no-stage")
        subprocess.run(["mindslice", "-w", str(self.out), "bake", "@demo"], check=True, capture_output=True)
        mounts = (self.out / "mounts.toml").read_text(encoding="utf-8")
        baked = [path for path in (self.out / "slices").glob("*.slice") if path.name != "demo.slice"]
        self.assertEqual(len(baked), 1)
        self.forget_calls()

        for args in ([], ["--from", "extract"], ["--from", "plan"]):
            code, _, err = self.forge(*NOVEL_ARGS, *args)
            self.assertEqual(code, 1)
            self.assertIn("changes that the forge did not make", err)
            self.assertIn("mounts.toml", err)
            self.assertIn(f"slices/{baked[0].name}/", err)
            self.assertEqual(self.calls(), [])
            self.assertEqual((self.out / "mounts.toml").read_text(encoding="utf-8"), mounts)
            self.assertTrue((baked[0] / "story/hero-note.md").is_file())
            self.assertEqual(self.state()["chunks_done"], ["0001"])

    # -- --into: a build for an existing workspace ---------------------------

    def test_build_into_an_existing_workspace_sees_it_and_is_added_to_it(self):
        target = self.target()
        memory = self.kept(target)
        code, out, err = self.forge(*NOVEL_ARGS, "--into", target)
        self.assertEqual(code, 0, err)
        # The existing workspace's own problem is not the build's to fix: no repair session.
        self.assertEqual(self.calls(), ["plan", "extract-0001", "extract-0002", "extract-0003", "finalize"])
        # Sessions work with a copy of it, and are told so.
        self.assertEqual(self.mounted(self.out)["@lore"], "./reference/lore.slice")
        self.assertTrue((self.out / "overlay/notes/recent.md").is_file())
        hits = self.mindslice("search", "Hero")["results"]
        self.assertIn("@lore/people/hero", [hit["address"] for hit in hits])
        for step in ("plan", "extract-0002", "finalize"):
            self.assertIn("## The existing workspace", self.prompt(step))
            self.assertIn(f"`@lore`, 2 cards: `{self.out / 'reference/lore.slice'}`", self.prompt(step))
            self.assertIn("1 recent card(s)", self.prompt(step))
        # The finished slice is copied into the existing workspace, mounted, and extends what was there.
        self.assertEqual(self.mounted(target)["@demo"], "./slices/demo.slice")
        self.assertIn(f"Added @demo to {target}", out)
        self.assertIn(f"mindslice -w {target} doctor", out)
        self.assertIn("@lore/people/hero", json.dumps(self.mindslice("read", "@demo/story/hero", workspace=target)))
        report = self.mindslice("validate", workspace=target)
        self.assertEqual([i for i in report["errors"] + report["warnings"] if i.get("address", "").startswith("@demo")], [])
        self.assertEqual(self.kept(target), memory)
        # Running again changes nothing.
        self.forget_calls()
        self.assertEqual(self.forge(*NOVEL_ARGS, "--into", target)[0], 0)
        self.assertEqual(self.calls(), [])
        self.assertFalse((target / "slices/demo-2.slice").exists())

    def test_connect_resumes_after_the_second_mount_fails(self):
        target = self.tmp / "target"
        for ws in (self.out, target):
            subprocess.run(["mindslice", "-w", str(ws), "init"], check=True, capture_output=True)
        for alias in ("first", "second"):
            source = self.out / "slices" / (alias + ".slice")
            source.mkdir()
            (source / "sample.md").write_text("Sample.")
            pipeline.engine.mount(self.out, source, alias=alias)
        job = pipeline.Job([], [], "", self.out, [], into=target)
        job.work.mkdir()
        state = {"chunks_done": ["one"]}
        mount = pipeline.engine.mount

        def flaky(ws, path, alias):
            if alias == "@second":
                raise OSError("interrupted")
            return mount(ws, path, alias=alias)

        with mock.patch.object(pipeline.engine, "mount", flaky), self.assertRaises(OSError):
            pipeline.connect(job, state, 1)
        with contextlib.redirect_stdout(io.StringIO()):
            pipeline.connect(job, json.loads(job.state_file.read_text()), 1)
        self.assertEqual({m["alias"] for m in pipeline.engine.mounts(target)}, {"@overlay", "@first", "@second"})
        self.assertTrue(json.loads(job.state_file.read_text())["connected"])

    def test_sample_build_is_added_only_when_complete(self):
        target = self.target()
        code, out, err = self.forge(*NOVEL_ARGS, "--sample", "1", "--into", target)
        self.assertEqual(code, 0, err)
        self.assertIn(f"Not added to {target}: 1 of 3 chunks", out)
        self.assertNotIn("@demo", self.mounted(target))
        code, out, err = self.forge(*NOVEL_ARGS, "--into", target)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.mounted(target)["@demo"], "./slices/demo.slice")

    def test_change_to_the_copy_of_the_existing_workspace_is_undone_and_sent_back(self):
        target = self.target()
        code, _, err = self.forge(*NOVEL_ARGS, "--sample", "1", "--into", target, modes="touch_reference")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), ["plan", "extract-0001", "extract-0001.repair1", "finalize"])
        repair = self.prompt("extract-0001.repair1")
        self.assertIn("copy of the existing workspace", repair)
        for name in ("hero.md", "new.md"):
            self.assertIn(str(self.out / "reference/lore.slice/people" / name), repair)
        self.assertEqual((self.out / "reference/lore.slice/people/hero.md").read_bytes(),
                         (target / "slices/lore.slice/people/hero.md").read_bytes())
        self.assertFalse((self.out / "reference/lore.slice/people/new.md").exists())
        self.assertEqual(checkpoint.git(self.out, "status", "--porcelain"), "")

    def test_alias_the_existing_workspace_uses_is_sent_back(self):
        target = self.target(alias="@demo")
        code, _, err = self.forge(*NOVEL_ARGS, "--into", target)
        self.assertEqual(code, 1)
        self.assertIn("the alias @demo is taken", err)
        self.assertEqual(self.calls(), ["plan", "plan.repair1", "plan.repair2"])

    def test_rebuild_is_added_again_only_after_the_earlier_one_is_unmounted(self):
        target = self.target()
        self.assertEqual(self.forge(*NOVEL_ARGS, "--into", target)[0], 0)
        code, _, err = self.forge(*NOVEL_ARGS, "--into", target, "--from", "finalize")
        self.assertEqual(code, 1)
        self.assertIn(f"@demo is mounted in {target} already", err)
        self.assertIn(f"mindslice -w {target} unmount @demo", err)
        subprocess.run(["mindslice", "-w", str(target), "unmount", "@demo"], check=True, capture_output=True)
        code, _, err = self.forge(*NOVEL_ARGS, "--into", target)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.mounted(target)["@demo"], "./slices/demo-2.slice")

    def test_build_continues_only_with_the_into_it_was_started_with(self):
        target = self.target()
        self.assertEqual(self.forge(*NOVEL_ARGS, "--sample", "1")[0], 0)
        code, _, err = self.forge(*NOVEL_ARGS, "--into", target)
        self.assertEqual(code, 1)
        self.assertIn("started without --into", err)
        self.assertNotIn("@demo", self.mounted(target))

    def test_dry_run_names_the_existing_workspace_and_changes_nothing(self):
        target = self.target()
        memory = self.kept(target)
        code, out, err = self.forge(*NOVEL_ARGS, "--dry-run", "--into", target)
        self.assertEqual(code, 0, err)
        self.assertIn(f"Adds the result to: {target}, which mounts @overlay, @lore", out)
        self.assertFalse(self.out.exists())
        self.assertEqual(self.kept(target), memory)

    def test_workspace_without_its_checkpoints_is_not_rolled_back(self):
        self.assertEqual(self.forge(*NOVEL_ARGS, "--sample", "1")[0], 0)
        shutil.rmtree(self.out / ".git")
        code, _, err = self.forge(*NOVEL_ARGS)
        self.assertEqual(code, 1)
        self.assertIn("has no forge checkpoints", err)
        self.assertTrue((self.out / "slices/demo.slice/story/hero.md").is_file())

    def test_changed_prompt_is_refused_until_a_stage_is_redone(self):
        self.assertEqual(self.forge(*NOVEL_ARGS, "--sample", "1", "--prompt", "世界观")[0], 0)
        self.forget_calls()
        code, _, err = self.forge(*NOVEL_ARGS, "--sample", "1", "--prompt", "人物关系")
        self.assertEqual(code, 1)
        self.assertIn("changed since this output was started", err)
        self.assertEqual(self.calls(), [])

        code, _, err = self.forge(*NOVEL_ARGS, "--sample", "1", "--prompt", "人物关系", "--from", "extract")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), ["extract-0001", "finalize"])
        self.assertEqual(self.commits(), ["empty workspace", "plan", CHUNK_COMMITS[0], "finalize"])
        self.assertIn("人物关系", (self.work / "prompts" / "extract-0001.md").read_text(encoding="utf-8"))

    def test_from_plan_redoes_everything(self):
        self.assertEqual(self.forge(*NOVEL_ARGS, "--sample", "1")[0], 0)
        self.forget_calls()
        code, _, err = self.forge(*NOVEL_ARGS, "--sample", "1", "--from", "plan")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), ["plan", "extract-0001", "finalize"])
        self.assertEqual(self.commits(), ["empty workspace", "plan", CHUNK_COMMITS[0], "finalize"])
        self.assertTrue(self.mindslice("validate")["ok"])

    def test_from_split_applies_a_new_chunk_size_without_a_new_plan(self):
        self.assertEqual(self.forge(*NOVEL_ARGS, "--sample", "1")[0], 0)
        self.forget_calls()
        code, out, err = self.forge("--chunk-size", "300", "--from", "split")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), ["extract-0001", "extract-0002", "finalize"])
        self.assertIn("2 of 2 chunks processed", out)

    def test_new_harness_command_is_checked_once_before_the_build(self):
        code, out, err = self.forge(*NOVEL_ARGS, "--sample", "1")
        self.assertEqual(code, 0, err)
        self.assertLess(out.index("[harness] the command works"), out.index("[plan] done"))
        self.assertEqual(self.probes(), 1)
        self.assertFalse((self.out / "forge-probe.txt").exists())
        self.assertEqual(self.commits(), ["empty workspace", "plan", CHUNK_COMMITS[0], "finalize"])

        self.assertEqual(self.forge(*NOVEL_ARGS, "--sample", "2")[0], 0)  # the same command: not checked again
        self.assertEqual(self.forge(*NOVEL_ARGS, "--sample", "2")[0], 0)  # nothing left to do
        self.assertEqual(self.probes(), 1)
        self.assertEqual(self.forge(*NOVEL_ARGS, "--sample", "2", harness_cmd=FAKE + " --model other")[0], 0)
        self.assertEqual(self.probes(), 1)  # a new command, but no session to run with it
        self.assertEqual(self.forge(*NOVEL_ARGS, harness_cmd=FAKE + " --model other")[0], 0)
        self.assertEqual(self.probes(), 2)

    def test_harness_command_that_cannot_do_the_work_stops_before_the_build(self):
        exits = f'{sys.executable} -c "import sys; sys.exit(3)" {{prompt_file}}'
        for options, reasons in (({"modes": "probe_silent"}, ["did not write"]),
                                 ({"modes": "probe_blind"}, ["on its PATH", "must be able to read the sources"]),
                                 ({"harness_cmd": exits}, ["did not end with exit status 0", "exit status 3"])):
            code, _, err = self.forge(*NOVEL_ARGS, **options)
            self.assertEqual(code, 1, options)
            self.assertIn("the harness command cannot run this build", err)
            for reason in reasons:
                self.assertIn(reason, err)
            self.assertIn("mindslice-forge --help", err)
            self.assertEqual(self.calls(), [])
            self.assertFalse(self.state()["plan"])
            self.assertFalse((self.out / "forge-probe.txt").exists())
            self.assertEqual(self.commits(), ["empty workspace"])
        code, _, err = self.forge(*NOVEL_ARGS, "--sample", "1")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), ["plan", "extract-0001", "finalize"])

    def test_dry_run_calls_no_agent_and_writes_nothing(self):
        argv = [str(self.novel), "-o", str(self.out), *NOVEL_ARGS, "--dry-run"]  # needs no harness command
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(cli.main(argv), 0)
        self.assertIn("(no --harness-cmd given)", stdout.getvalue())
        for directory in (self.work, self.out, self.tmp):
            self.assertIn(f"- `{directory}` (", stdout.getvalue())

        code, out, err = self.forge(*NOVEL_ARGS, "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("gb18030", out)
        self.assertIn("Splitting: needed", out)
        self.assertFalse(self.out.exists())
        self.assertFalse(self.work.exists())

    def test_existing_directory_is_never_taken_over(self):
        self.out.mkdir()
        (self.out / "precious.txt").write_text("keep me", encoding="utf-8")
        code, _, err = self.forge(*NOVEL_ARGS)
        self.assertEqual(code, 1)
        self.assertIn("was not created by the forge", err)
        self.assertEqual((self.out / "precious.txt").read_text(encoding="utf-8"), "keep me")
        self.assertEqual(self.calls(), [])

    def test_missing_harness_command_is_reported(self):
        argv = [str(self.novel), "-o", str(self.out), "--harness-cmd", "no-such-harness-command {prompt_file}"]
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            self.assertEqual(cli.main(argv), 1)
        self.assertIn("harness command not found: no-such-harness-command", stderr.getvalue())


@unittest.skipUnless(shutil.which("git"), "needs git")
class LookupCheckpointTest(unittest.TestCase):
    def test_interrupted_rollback_preserves_later_edits_and_git_excludes(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            checkpoint.init(out)
            exclude = out / ".git/info/exclude"
            with exclude.open("a") as f:
                f.write("/user-cache/\n")
            checkpoint.commit(out, "baseline")
            checkpoint.begin(out)
            note = out / "notes.txt"
            note.write_text("build output")
            checkpoint.record_failed_attempt(out)
            note.write_text("edited after interruption")
            with self.assertRaises(ForgeError):
                checkpoint.rollback(out)
            self.assertEqual(note.read_text(), "edited after interruption")
            self.assertIn("/user-cache/", exclude.read_text())

    def test_ctrl_c_during_a_session_is_discarded_on_the_next_run(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            checkpoint.init(out)
            checkpoint.commit(out, "baseline")
            checkpoint.begin(out)
            job = pipeline.Job(sources=[], dicts=[], prompt="", out=out, harness=[])

            def interrupted(*args):
                (out / "partial.md").write_text("half a card")
                raise KeyboardInterrupt

            with mock.patch.object(pipeline.harness, "run", interrupted), self.assertRaises(KeyboardInterrupt):
                pipeline.run_harness(job, ["agent"], out / "log", 1)
            checkpoint.rollback(out)
            self.assertFalse((out / "partial.md").exists())

    def test_new_cards_include_staged_and_renamed_files_but_not_existing_edits(self):
        with tempfile.TemporaryDirectory(prefix="forge-checkpoint-") as directory:
            out = Path(directory).resolve()
            root = out / "slices/demo.slice"
            root.mkdir(parents=True)

            def card(name, title, aliases=()):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f'+++\ntitle = "{title}"\naliases = {json.dumps(aliases)}\n+++\n\nSummary.\n')
                return path

            old = card("old.md", "Old")
            original = card("original.md", "Original")
            deleted = card("deleted.md", "Deleted")
            checkpoint.init(out)
            checkpoint.commit(out, "baseline")
            card("old.md", "Changed")
            deleted.unlink()
            renamed = root / "renamed.md"
            checkpoint.git(out, "mv", str(original), str(renamed))
            staged = card("staged.md", "Staged")
            checkpoint.git(out, "add", str(staged))
            untracked = card("space and\n换行.md", "Database", ["Storage"])
            overview = root / "overview.md"
            overview.write_text("# Overview\n")
            nested = card("topic/overview.md", "Topic overview")

            self.assertEqual(checkpoint.created(out), {renamed, staged, untracked, overview, nested})
            self.assertEqual(checkpoint.changed(out), {old, original, deleted, renamed, staged, untracked, overview, nested})
            job = pipeline.Job(sources=[], dicts=[], prompt="", out=out, harness=[])
            issues = pipeline.not_looked_up(job, ["Storage"])
            self.assertEqual({Path(issue["file"]) for issue in issues}, {renamed, staged, nested})
            self.assertTrue(all(issue["code"] == "not_looked_up" for issue in issues))


if __name__ == "__main__":
    unittest.main()
