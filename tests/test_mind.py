"""Earth Mind: the coordination layer (docs/EARTH_MIND.md).

Five areas — the router, model management, the memory store, personalities,
and the coordinator itself. The game subsystem has its own file
(``test_mind_game.py``), as does the terminal (``test_mind_terminal.py``).

The model is a recording fake or a local server speaking Ollama's API; the
shell, the capability layer, SQLite and the World Model's ledger are real.
"""

from __future__ import annotations

import ast
import asyncio
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from mind_helpers import (FakeModel, FakeOllama, FakePort, bare_mind, dead_host,   # noqa: E402
                          fake_system, ledger_in_tmp, temp_config)

from earth_os.core import Shell                                                    # noqa: E402
from earth_os.mind import (MemoryAccessError, MemoryStore, ModelManager, ModelSpec,  # noqa: E402
                           Personality, PersonalityError, PersonalityRegistry, Principal,
                           Router, build_mind)
from earth_os.mind import envelope as env                                          # noqa: E402
from earth_os.mind.memory import (MIND, MemoryFormatError, allowed,                # noqa: E402
                                  character_namespace, conversation_namespace,
                                  events_namespace, world_namespace)
from earth_os.mind.redact import MARK, redact                                      # noqa: E402
from earth_os.mind.system import ACTION, ANALYSIS, DIAGNOSIS, PROGRAM, READING     # noqa: E402
from earth_os.world_model import parse_request                                     # noqa: E402

_SHELL = {}


def shell() -> Shell:
    """One real shell for the module: building it takes a platform adapter and a corpus."""
    if "shell" not in _SHELL:
        _SHELL["shell"] = Shell(temp_config())
        _SHELL["shell"].sound.enabled = False
    return _SHELL["shell"]


# ===========================================================================
# 1. The router: six kinds of request, decided with no model
# ===========================================================================
class TestRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mind = shell().earth_mind()
        assert cls.mind is not None, shell().mind_error
        cls.router = cls.mind.router

    def kind(self, text):
        route = self.router.route(text)
        return route.kind, route.subsystems

    def test_the_examples_in_the_specification(self):
        self.assertEqual(self.kind("What is using the most RAM?"),
                         (env.SYSTEM_QUERY, (env.SYSTEM,)))
        self.assertEqual(self.kind("Explain how neural networks work."),
                         (env.KNOWLEDGE, (env.CDOS, env.COMPANION)))
        self.assertEqual(self.kind("Why is my computer running slowly?"),
                         (env.MIXED, (env.SYSTEM, env.COMPANION)))
        self.assertEqual(self.kind("Talk to the shopkeeper."), (env.GAME, (env.GAME_ENGINE,)))
        self.assertEqual(self.kind("I had a long day and I am tired"),
                         (env.CONVERSATION, (env.COMPANION,)))

    def test_a_system_action_is_recognised_as_one(self):
        route = self.router.route("shut down")
        self.assertEqual(route.kind, env.SYSTEM_ACTION)
        self.assertEqual(route.system.kind, ACTION)
        self.assertTrue(route.system.effectful)

    def test_open_a_program_only_when_it_is_installed(self):
        """"Open Firefox" names a program, and only one this machine has."""
        router = Router(system=fake_system(programs={"firefox": "Firefox"}))
        route = router.route("Open Firefox.")
        self.assertEqual((route.kind, route.system.kind, route.system.program),
                         (env.SYSTEM_ACTION, PROGRAM, "Firefox"))
        self.assertTrue(route.priority)
        nothing = Router(system=fake_system()).route("Open Firefox.")
        self.assertIsNone(nothing.system, "a program that is not installed is not invented")

    def test_readings_analyses_and_diagnoses_are_told_apart(self):
        self.assertEqual(self.router.route("what is the cpu load").system.kind, READING)
        self.assertEqual(self.router.route("what is using the most memory").system.kind, ANALYSIS)
        self.assertEqual(self.router.route("why is my computer slow").system.kind, DIAGNOSIS)

    def test_talk_that_contains_a_system_word_is_talk(self):
        route = self.router.route("I was thinking about the volume of my life lately")
        self.assertEqual(route.kind, env.CONVERSATION)
        self.assertIsNone(route.system)

    def test_distress_outranks_everything(self):
        route = self.router.route("my chest hurts and the cpu is hot")
        self.assertEqual(route.kind, env.CONVERSATION)
        self.assertIn("sensitive", route.why[0])

    def test_remember_goes_to_the_world_model_outside_a_game(self):
        router = Router(world_requests=parse_request)
        self.assertEqual(router.route("remember that I prefer tea").control, "memory")

    def test_a_personality_is_selected_by_name(self):
        route = self.router.route("switch to the technical assistant")
        self.assertEqual((route.control, route.target.id), ("persona", "technician"))

    def test_inside_a_conversation_with_a_character_everything_is_theirs(self):
        environment, elara = self.mind.games.find_character("elara")
        held = (environment, elara)
        for text in ("what is the cpu load", "shut down", "open firefox", "what is 2 plus 2"):
            route = self.router.route(text, in_game=held)
            self.assertEqual(route.kind, env.GAME, text)
            self.assertIsNone(route.system, f"{text!r} reached the machine from inside a game")
        self.assertEqual(self.router.route("goodbye", in_game=held).control, "leave")
        self.assertEqual(self.router.route("Remember that I helped this character.", in_game=held).control,
                         "note")
        self.assertEqual(self.router.route("three days later", in_game=held).argument, "3")
        self.assertEqual(self.router.route("talk to brann", in_game=held).target[1].id, "brann")

    def test_nothing_said_is_not_an_error(self):
        self.assertEqual(self.router.route("  ?! ").why, ["nothing said"])

    def test_routing_calls_no_model(self):
        model = FakeModel()
        mind = bare_mind(model=model, system=fake_system())
        for text in ("hello", "why is my computer slow", "talk to elara", "what is 2 plus 2"):
            mind.route(text)
        self.assertEqual(model.calls, [])


# ===========================================================================
# 2. Model management: one model, loaded once, shared by every voice
# ===========================================================================
class TestModels(unittest.TestCase):
    def test_a_spec_is_parsed(self):
        spec = ModelSpec.parse("ollama:llama3.2:3b@http://10.0.0.5:11434/")
        self.assertEqual((spec.provider, spec.model, spec.host),
                         ("ollama", "llama3.2:3b", "http://10.0.0.5:11434"))
        self.assertEqual(ModelSpec.parse("").provider, "none")
        self.assertEqual(str(ModelSpec.parse("ollama:phi3")), "ollama:phi3")

    def test_every_environment_shares_one_client(self):
        models = ModelManager(default="ollama:llama3.2:3b", options={"host": dead_host()})
        a = models.model("os")
        b = models.model("game")
        c = models.model("game:harbour")
        self.assertIs(a.managed, b.managed)
        self.assertIs(b.managed, c.managed)
        self.assertEqual(len(models.clients), 1, "a second environment loaded a second model")

    def test_a_game_can_be_given_a_smaller_model(self):
        models = ModelManager(default="ollama:llama3.2:3b", assignments={"game": "ollama:llama3.2:1b"},
                              options={"host": dead_host()})
        self.assertEqual(str(models.spec_for("game:harbour")), "ollama:llama3.2:1b")
        self.assertEqual(str(models.spec_for("os")), "ollama:llama3.2:3b")
        models.model("os"), models.model("game:harbour"), models.model("game:other")
        self.assertEqual(len(models.clients), 2)

    def test_the_existing_companion_settings_still_choose_the_model(self):
        config = temp_config()
        config.values["companion"]["model"] = "llama3.2:1b"
        self.assertEqual(str(ModelManager.from_config(config).default), "ollama:llama3.2:1b")
        config.values["mind"]["model"] = "ollama:phi3:mini"
        self.assertEqual(str(ModelManager.from_config(config).default), "ollama:phi3:mini")

    def test_a_new_provider_is_one_function(self):
        models = ModelManager(default="remote:big")
        seen = []
        client = FakeModel("from far away")
        models.register_provider("remote", lambda spec, options: seen.append(spec) or client)
        self.assertEqual(models.model("os").chat([{"role": "user", "content": "hi"}]), "from far away")
        models.model("game")
        self.assertEqual(len(seen), 1, "the provider was asked to connect twice")

    def test_an_unknown_provider_is_reported_not_raised(self):
        models = ModelManager(default="nonesuch:x")
        self.assertIsNone(models.model("os"))
        self.assertIn("nonesuch", " ".join(models.errors.values()))
        self.assertFalse(models.available("os"))

    def test_secrets_never_reach_a_model(self):
        models = ModelManager()
        client = FakeModel()
        model = models.use(client)
        model.chat([{"role": "system", "content": "The process runs with --password hunter2secret"},
                    {"role": "user", "content": "my key is sk-abcdefghijklmnopqrstuvwxyz012345 ok?"}])
        sent = client.everything()
        self.assertNotIn("hunter2secret", sent)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz012345", sent)
        self.assertEqual(sent.count(MARK), 2)
        self.assertEqual(models.usage()["os"]["redactions"], 2)

    def test_redaction_leaves_ordinary_sentences_alone(self):
        for text in ("The password is wrong.", "What is a token in a language model?",
                     "Memory is 62% full: 9.9 GB of 16.0 GB in use.",
                     "/usr/lib/python3.12/site-packages/gi/overrides/__init__.py"):
            self.assertEqual(redact(text), (text, 0), text)
        for secret in ("AKIAIOSFODNN7EXAMPLE", "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
                       "postgres://admin:s3cretpw@db.internal/app",
                       "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
                       "API_KEY=9f8e7d6c5b4a"):
            cleaned, n = redact(f"here: {secret} end")
            self.assertGreaterEqual(n, 1, secret)
            self.assertIn(MARK, cleaned)

    def test_one_generation_at_a_time_per_model(self):
        """Twelve characters asking at once queue behind one model; they do not thrash it."""
        models = ModelManager()
        client = FakeModel(delay=0.02)
        models.use(client)
        threads = [threading.Thread(target=lambda e=e: models.model(e).chat(
            [{"role": "user", "content": "hi"}])) for e in ["os"] + [f"game:w{i}" for i in range(11)]]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(client.calls), 12)
        self.assertEqual(client.most_at_once, 1)
        usage = models.usage()
        self.assertEqual(sum(u["calls"] for u in usage.values()), 12)
        self.assertGreater(sum(u["waited"] for u in usage.values()), 0.0)

    def test_usage_is_counted_per_environment(self):
        models = ModelManager()
        models.use(FakeModel("ok"))
        models.model("os").chat([{"role": "user", "content": "one"}])
        models.model("game:harbour").chat([{"role": "user", "content": "two"}])
        models.model("game:harbour").chat([{"role": "user", "content": "three"}])
        usage = models.usage()
        self.assertEqual((usage["os"]["calls"], usage["game:harbour"]["calls"]), (1, 2))

    def test_a_failing_model_is_counted_and_the_error_is_the_callers(self):
        models = ModelManager()
        models.use(FakeModel(fail=True))
        with self.assertRaises(TimeoutError):
            models.model("os").chat([{"role": "user", "content": "hi"}])
        self.assertEqual(models.usage()["os"]["failures"], 1)

    def test_building_the_manager_contacts_nothing(self):
        server = FakeOllama()
        try:
            models = ModelManager(default="ollama:llama3.2:3b", options={"host": server.host})
            models.model("os"), models.model("game")
            models.status_rows()
            self.assertEqual((server.tags_hits, server.payloads), (0, []))
        finally:
            server.close()

    def test_the_real_ollama_client_is_shared_by_the_os_and_a_game(self):
        server = FakeOllama()
        try:
            models = ModelManager(default="ollama:llama3.2:3b", options={"host": server.host})
            os_model, game_model = models.model("os"), models.model("game:harbour")
            self.assertTrue(os_model.available)
            os_model.chat([{"role": "user", "content": "hello"}])
            game_model.chat([{"role": "user", "content": "hello, shopkeeper"}])
            self.assertEqual(len(server.payloads), 2)
            self.assertEqual({p["model"] for p in server.payloads}, {"llama3.2:3b"})
            self.assertEqual({p["options"]["num_ctx"] for p in server.payloads}, {4096},
                             "a changed num_ctx makes Ollama reload the model")
            self.assertEqual(server.tags_hits, 1, "availability is cached, not asked per voice")
        finally:
            server.close()

    def test_an_offline_model_is_unavailable_not_an_exception(self):
        models = ModelManager(default="ollama:llama3.2:3b", options={"host": dead_host(), "timeout": 2})
        self.assertFalse(models.available("os"))
        self.assertIn("ollama", models.describe().lower())


# ===========================================================================
# 3. Unified memory: one store, namespaces, a principal on every access
# ===========================================================================
class TestMemory(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.mind = self.store.view(MIND)
        self.elara = Principal("character", "elara", "harbour", frozenset({"converse"}))
        self.brann = Principal("character", "brann", "harbour", frozenset({"converse"}))
        self.game = Principal("game", world="harbour")
        self.other_game = Principal("game", world="moonbase")
        self.companion = Principal("persona", "companion", "",
                                   frozenset({"converse", "memory.user", "system.read"}))
        self.technician = Principal("persona", "technician", "", frozenset({"converse", "system.read"}))

    def test_the_seven_kinds_of_memory_have_separate_places(self):
        names = ["user", "conversation/companion", "system", "knowledge",
                 character_namespace("harbour", "elara"), world_namespace("harbour"),
                 events_namespace("harbour")]
        self.assertEqual(len(set(names)), 7)
        from earth_os.mind.memory import check_namespace
        for name in names:
            check_namespace(name)
        for bad in ("", "everything", "character/elara", "world/", "character/a/b/c", "world/Has Space",
                    "../etc", "events/x;drop"):
            with self.assertRaises(MemoryFormatError, msg=bad):
                check_namespace(bad)

    def test_a_character_reads_and_writes_only_its_own_memories(self):
        mine = character_namespace("harbour", "elara")
        theirs = character_namespace("harbour", "brann")
        self.store.view(self.elara).add(mine, "The traveller paid in silver.")
        self.store.view(self.game).add(theirs, "Brann hid the lighthouse key.")
        self.assertEqual([r.text for r in self.store.view(self.elara).recent(mine)],
                         ["The traveller paid in silver."])
        for op in (lambda v: v.recent(theirs), lambda v: v.recall(theirs, "key"),
                   lambda v: v.add(theirs, "planted"), lambda v: v.find(theirs, "memory", ""),
                   lambda v: v.count(theirs), lambda v: v.clear(theirs)):
            with self.assertRaises(MemoryAccessError):
                op(self.store.view(self.elara))
        self.assertEqual(self.store.view(self.game).count(theirs), 1, "a refused write left a record")

    def test_a_refusal_is_recorded_and_is_never_an_empty_answer(self):
        theirs = character_namespace("harbour", "brann")
        with self.assertRaises(MemoryAccessError):
            self.store.view(self.elara).recent(theirs)
        self.assertEqual(self.store.denials[-1], ("character:harbour/elara", "read", theirs))

    def test_a_character_cannot_reach_the_person_the_machine_or_another_world(self):
        view = self.store.view(self.elara)
        for namespace in ("user", "system", "events/system", "conversation/companion",
                          "events/harbour", "world/moonbase", character_namespace("moonbase", "elara")):
            for op in ("read", "write", "forget"):
                self.assertFalse(allowed(self.elara, op, namespace), f"{op} {namespace}")
            with self.assertRaises(MemoryAccessError):
                view.recent(namespace)

    def test_a_character_sees_only_the_public_facts_of_its_world(self):
        world = world_namespace("harbour")
        game = self.store.view(self.game)
        game.add(world, "The lighthouse is dark.", kind="fact", visibility="public")
        game.add(world, "Vex has the ledger.", kind="fact", visibility="private")
        seen = [r.text for r in self.store.view(self.elara).recent(world)]
        self.assertEqual(seen, ["The lighthouse is dark."])
        self.assertEqual(self.store.view(self.elara).count(world), 1)
        with self.assertRaises(MemoryAccessError):
            self.store.view(self.elara).add(world, "I own the harbour.", kind="fact", visibility="public")

    def test_a_game_is_confined_to_its_own_world(self):
        game = self.store.view(self.game)
        game.add(world_namespace("harbour"), "fog", kind="fact")
        game.add(events_namespace("harbour"), "A boat sank.", kind="event")
        game.add(character_namespace("harbour", "elara"), "saw it", kind="event")
        for namespace in ("user", "system", "events/system", "conversation/companion",
                          world_namespace("moonbase"), events_namespace("moonbase"),
                          character_namespace("moonbase", "ada")):
            with self.assertRaises(MemoryAccessError, msg=namespace):
                game.recent(namespace)
            with self.assertRaises(MemoryAccessError, msg=namespace):
                game.add(namespace, "x")
        with self.assertRaises(MemoryAccessError):
            self.store.view(self.other_game).recent(world_namespace("harbour"))
        self.assertFalse(allowed(Principal("game", world="system"), "write", "events/system"),
                         "a world called 'system' could write the machine's event history")

    def test_history_is_append_only_even_for_the_coordinator(self):
        events = events_namespace("harbour")
        record = self.store.view(self.game).add(events, "The light went out.", kind="event")
        for view in (self.store.view(self.game), self.mind):
            with self.assertRaises(MemoryAccessError):
                view.forget(events, record.id)
            with self.assertRaises(MemoryAccessError):
                view.clear(events)
        with self.assertRaises(MemoryFormatError):
            self.store.view(self.game).put(events, "event", "x", "rewritten")
        self.assertEqual(self.mind.count(events), 1)

    def test_an_os_personality_has_its_own_conversation_and_no_game(self):
        mine = conversation_namespace("companion")
        self.store.view(self.companion).add(mine, "we talked about tea", kind="turn")
        with self.assertRaises(MemoryAccessError):
            self.store.view(self.technician).recent(mine)
        for namespace in (character_namespace("harbour", "elara"), world_namespace("harbour"),
                          events_namespace("harbour")):
            with self.assertRaises(MemoryAccessError):
                self.store.view(self.companion).recent(namespace)

    def test_system_memory_needs_the_permission_and_is_read_only_to_a_personality(self):
        self.mind.add("system", "Memory was 91% full.", kind="diagnosis")
        self.assertEqual(len(self.store.view(self.technician).recent("system")), 1)
        with self.assertRaises(MemoryAccessError):
            self.store.view(self.technician).add("system", "all is well")
        plain = Principal("persona", "poet", "", frozenset({"converse"}))
        with self.assertRaises(MemoryAccessError):
            self.store.view(plain).recent("system")

    def test_the_knowledge_corpus_is_not_stored_or_writable_here(self):
        for principal in (MIND, self.companion, self.game, self.elara):
            for op in ("read", "write", "forget"):
                self.assertFalse(allowed(principal, op, "knowledge"))

    def test_user_memory_is_the_ledger_and_can_only_be_suggested_to(self):
        ledger = ledger_in_tmp()
        ledger.remember("prefers tea to coffee", kind="preference")
        store = MemoryStore(ledger=ledger)
        view = store.view(self.companion)
        self.assertEqual([r.text for r in view.recent("user")], ["prefers tea to coffee"])
        noted = view.add("user", "is learning to sail", kind="activity")
        self.assertEqual(noted.data["status"], "pending")
        self.assertEqual([r.text for r in view.recent("user")], ["prefers tea to coffee"],
                         "a pending note was read back as a memory")
        self.assertEqual(len(ledger.pending()), 1)
        with self.assertRaises(MemoryAccessError):
            view.clear("user")
        with self.assertRaises(MemoryAccessError):
            store.view(self.technician).recent("user")

    def test_recall_prefers_what_bears_on_the_question(self):
        ns = character_namespace("harbour", "elara")
        view = self.store.view(self.elara)
        view.add(ns, "The traveller bought six flasks of lamp oil.", kind="event", importance=0.6)
        view.add(ns, "A gull stole a fish from the quay.", kind="event", importance=0.6)
        view.add(ns, "Brann still owes forty marks.", kind="memory", importance=0.6)
        self.assertIn("lamp oil", view.recall(ns, "did I buy lamp oil from you?", limit=1)[0].text)
        self.assertEqual(view.recall(ns, "zeppelin", limit=3), [])
        self.assertEqual(len(view.recall(ns, "zeppelin", limit=3, always=2)), 2)

    def test_keyed_state_is_one_record_however_often_it_changes(self):
        world = world_namespace("harbour")
        game = self.store.view(self.game)
        for day in (1, 2, 3):
            game.put(world, "state", "day", f"day is {day}", data={"value": day})
        self.assertEqual(game.count(world), 1)
        self.assertEqual(game.find(world, "state", "day").data["value"], 3)

    def test_chatter_is_bounded_but_events_are_kept(self):
        import earth_os.mind.memory as memory
        old = memory.MAX_CHATTER
        memory.MAX_CHATTER = 20
        try:
            ns = character_namespace("harbour", "elara")
            game = self.store.view(self.game)
            game.add(ns, "The lighthouse was relit.", kind="event", importance=0.9)
            for i in range(60):
                game.add(ns, f"the traveller said thing {i}", kind="heard", importance=0.35)
            kinds = [r.kind for r in game.recent(ns, limit=500)]
            self.assertEqual(kinds.count("heard"), 20)
            self.assertEqual(kinds.count("event"), 1)
        finally:
            memory.MAX_CHATTER = old

    def test_memories_persist_in_one_file_and_no_file_is_made_for_nothing(self):
        path = Path(tempfile.mkdtemp()) / "mind" / "memory.db"
        store = MemoryStore(path)
        self.assertEqual(store.size(), 0)
        self.assertFalse(path.exists(), "a mind that only talked created a database")
        store.view(self.game).add(events_namespace("harbour"), "The keeper returned.", kind="event",
                                  data={"day": 4})
        store.close()
        again = MemoryStore(path)
        record = again.view(self.game).recent(events_namespace("harbour"))[0]
        self.assertEqual((record.text, record.data["day"], record.source),
                         ("The keeper returned.", 4, "game:harbour"))
        self.assertGreater(record.created, 0)
        again.close()

    def test_the_store_is_safe_from_many_threads(self):
        ns = events_namespace("harbour")
        game = self.store.view(self.game)
        threads = [threading.Thread(target=lambda i=i: [game.add(ns, f"event {i}-{j}", kind="event")
                                                        for j in range(25)]) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(game.count(ns), 200)


# ===========================================================================
# 4. Personalities: many identities, one model, no borrowed power
# ===========================================================================
class TestPersonalities(unittest.TestCase):
    def test_the_shipped_personalities_load(self):
        registry = PersonalityRegistry()
        self.assertEqual([p.id for p in registry.all()], ["companion", "robot", "technician"])
        self.assertEqual(registry.unreadable, {})
        self.assertEqual(registry.find("the technical assistant").id, "technician")
        self.assertEqual(registry.find("Robot Companion").id, "robot")

    def test_each_has_its_own_identity_namespace_and_permissions(self):
        registry = PersonalityRegistry()
        companion, technician = registry.get("companion"), registry.get("technician")
        self.assertNotEqual(companion.namespace, technician.namespace)
        self.assertEqual(companion.namespace, "conversation/companion")
        self.assertTrue(companion.may("memory.user"))
        self.assertFalse(technician.may("memory.user"))
        self.assertNotEqual(companion.style_lines(), technician.style_lines())

    def test_a_permission_that_does_not_exist_is_refused(self):
        with self.assertRaises(PersonalityError):
            Personality.build({"id": "root", "name": "Root", "permissions": ["converse", "shell.exec"]})

    def test_a_game_character_may_only_converse(self):
        for asked in (["converse", "system.read"], ["system.act"], ["memory.user"], ["knowledge"]):
            with self.assertRaises(PersonalityError, msg=str(asked)):
                Personality.build({"id": "vex", "name": "Vex", "permissions": asked},
                                  environment="game:harbour")
        ok = Personality.build({"id": "vex", "name": "Vex"}, environment="game:harbour")
        self.assertEqual(ok.permissions, frozenset({"converse"}))

    def test_the_namespace_is_derived_and_cannot_be_pointed_elsewhere(self):
        person = Personality.build({"id": "vex", "name": "Vex", "namespace": "user",
                                    "memory_namespace": "character/harbour/elara"},
                                   environment="game:harbour")
        self.assertEqual(person.namespace, "character/harbour/vex")
        self.assertEqual(person.principal(), Principal("character", "vex", "harbour", frozenset({"converse"})))

    def test_a_bad_id_or_environment_is_refused(self):
        for raw in ({"id": "../x", "name": "X"}, {"id": "", "name": "X"}, {"id": "x"}):
            with self.assertRaises(PersonalityError):
                Personality.build(raw)
        with self.assertRaises(PersonalityError):
            Personality.build({"id": "x", "name": "X"}, environment="root")

    def test_control_characters_do_not_reach_a_prompt(self):
        person = Personality.build({"id": "x", "name": "X", "description": "kind\x00\x1b[31m and \n\nsly"})
        self.assertNotIn("\x00", person.description)
        self.assertNotIn("\x1b", person.description)
        self.assertNotIn("\n", person.description)

    def test_the_persons_own_personalities_load_and_a_broken_one_is_reported(self):
        directory = Path(tempfile.mkdtemp())
        (directory / "poet.json").write_text('{"id": "poet", "name": "Poet", "style": "Speak in few words."}')
        (directory / "broken.json").write_text("{not json")
        (directory / "greedy.json").write_text('{"id": "greedy", "name": "G", "permissions": ["root"]}')
        registry = PersonalityRegistry([directory])
        self.assertEqual([p.id for p in registry.all()], ["poet"])
        self.assertEqual(len(registry.unreadable), 2)

    def test_a_game_character_cannot_be_registered_as_an_os_personality(self):
        registry = PersonalityRegistry()
        character = Personality.build({"id": "vex", "name": "Vex"}, environment="game:harbour")
        with self.assertRaises(PersonalityError):
            registry.add(character)


# ===========================================================================
# 5. Earth Mind: the coordinator
# ===========================================================================
class TestEarthMind(unittest.TestCase):
    def test_it_never_raises(self):
        mind = bare_mind(model=FakeModel(fail=True), system=fake_system())
        for text in ("", "hello there, how are you today?", "why is my computer slow",
                     "talk to elara", "x" * 5000, "\x00\x01", "remember that"):
            response = mind.handle(text)
            self.assertIsNotNone(response.text)

    def test_a_subsystem_that_breaks_is_a_response_not_a_crash(self):
        system = fake_system()
        system.port.answer_reading = lambda capability: 1 / 0
        response = bare_mind(system=system).handle("what is the cpu load")
        self.assertFalse(response.ok)
        self.assertIn("ZeroDivisionError", response.text)

    def test_a_reading_is_answered_from_the_machine_with_no_model(self):
        model = FakeModel()
        system = fake_system()
        response = bare_mind(model=model, system=system).handle("what is the cpu load")
        self.assertEqual((response.route, response.source, response.mode()),
                         (env.SYSTEM_QUERY, "readings", "SYSTEM"))
        self.assertEqual(system.port.readings, ["hardware.cpu_load"])
        self.assertEqual(model.calls, [])

    def test_a_diagnosis_is_readings_first_and_the_companion_explains_them(self):
        model = FakeModel("Your machine is busy: the processor is at 91% and memory is 88% full, "
                          "mostly firefox at about 6.2 GB.")
        system = fake_system()
        mind = bare_mind(model=model, system=system)
        response = mind.handle("Why is my computer running slowly?")
        self.assertEqual(system.port.analyses, ["performance_report"], "the readings were not taken once")
        self.assertEqual((response.route, response.subsystems, response.source),
                         (env.MIXED, (env.SYSTEM, env.COMPANION), "model"))
        self.assertTrue(response.checked)
        self.assertIn("91%", response.detail)
        self.assertIn("The processor is at 91%", model.everything(),
                      "the model was not given the verified findings")
        self.assertEqual(len(model.calls), 1)

    def test_a_model_that_invents_a_figure_is_overruled_by_the_readings(self):
        model = FakeModel("Your disk is 99% full and the processor is at 12%.")
        response = bare_mind(model=model, system=fake_system()).handle("why is my computer slow")
        self.assertNotIn("99%", response.text)
        self.assertIn("91%", response.text)
        self.assertEqual(response.source, "readings")
        self.assertEqual(response.subsystems, (env.SYSTEM,))

    def test_with_no_model_a_diagnosis_is_still_the_findings(self):
        for model in (None, FakeModel(available=False), FakeModel(fail=True)):
            response = bare_mind(model=model, system=fake_system()).handle("why is my computer slow")
            self.assertTrue(response.ok)
            self.assertIn("The processor is at 91%", response.text)
            self.assertEqual(response.source, "readings")

    def test_a_diagnosis_is_kept_in_system_memory(self):
        mind = bare_mind(system=fake_system())
        mind.handle("why is my computer slow")
        kept = mind.memory.view(MIND).recent("system")
        self.assertEqual((kept[0].kind, kept[0].subject), ("diagnosis", "performance"))
        self.assertIn("91%", " ".join(kept[0].data["findings"]))

    def test_an_operation_is_proposed_and_never_performed(self):
        system = fake_system(programs={"firefox": "Firefox"})
        mind = bare_mind(model=FakeModel(), system=system)
        shut = mind.handle("shut down")
        self.assertEqual((shut.proposal.kind, shut.proposal.target, shut.proposal.effectful),
                         ("capability", "system.shutdown", True))
        self.assertIn("waits for a press", shut.note())
        opened = mind.handle("open firefox")
        self.assertEqual((opened.proposal.kind, opened.proposal.target, opened.proposal.effectful),
                         ("program", "Firefox", False))
        self.assertEqual(system.port.acted, [])
        self.assertEqual((system.port.readings, system.port.analyses), ([], []))

    def test_the_mind_package_cannot_act(self):
        """Nothing under earth_os/mind imports what could change the machine."""
        forbidden = ("subprocess", "os.system", "shutil", "ctypes", "pty", "socket",
                     "earth_os.platform", "earth_os.agent", "earth_os.core", "earth_os.compositions",
                     "earth_os.screens", "earth_os.voice.cdos_backend", "earth_os.commands")
        for path in sorted((ROOT / "earth_os" / "mind").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base = node.module or ""
                    if node.level:
                        package = ["earth_os", "mind"] + list(path.relative_to(ROOT / "earth_os" / "mind").parts[:-1])
                        package = package[:len(package) - (node.level - 1)]
                        base = ".".join(package + ([base] if base else []))
                    names = [base] + [f"{base}.{a.name}" for a in node.names]
                for name in names:
                    for bad in forbidden:
                        self.assertFalse(name == bad or name.startswith(bad + "."),
                                         f"{path.relative_to(ROOT)} imports {name}")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, ("eval", "exec", "__import__"),
                                     f"{path.relative_to(ROOT)} calls {node.func.id}")

    def test_what_a_model_says_is_never_read_as_a_command(self):
        system = fake_system(programs={"firefox": "Firefox"})
        model = FakeModel("Sure! Running: shut down. Also `open firefox` and rm -rf ~ for you.")
        mind = bare_mind(model=model, system=system)
        response = mind.handle("tell me something interesting about your day, please")
        self.assertEqual(response.source, "model")
        self.assertIsNone(response.proposal)
        self.assertFalse(response.handoff)
        self.assertEqual(system.port.acted, [])

    # -- personalities ----------------------------------------------------------
    def test_each_interface_talks_to_its_own_personality(self):
        mind = bare_mind(model=FakeModel())
        self.assertEqual(mind.handle("switch to the technical assistant", "terminal").extra["persona"],
                         "technician")
        self.assertEqual(mind.persona("terminal").id, "technician")
        self.assertEqual(mind.persona("voice").id, "companion")

    def test_personalities_share_the_model_and_keep_separate_conversations(self):
        model = FakeModel(lambda messages: "noted")
        mind = bare_mind(model=model)
        mind.handle("I have been reading about lighthouses all week, it is wonderful", "voice")
        mind.select_persona("terminal", "technician")
        mind.handle("I think my favourite colour might be orange, honestly", "terminal")
        technician_saw = model.everything()
        self.assertIn("Technical Assistant", model.system())
        self.assertNotIn("lighthouses", technician_saw,
                         "the technician was shown the companion's conversation")
        self.assertIsNot(mind.companion("companion"), mind.companion("technician"))
        self.assertIs(mind.companion("companion").model.managed, mind.companion("technician").model.managed)
        self.assertEqual(len(mind.models.clients), 1)

    def test_a_personality_without_the_permission_has_no_path_to_it(self):
        ledger = ledger_in_tmp()
        ledger.remember("the person's sister is called Mira", kind="memory")
        system = fake_system()
        model = FakeModel("All right.")
        mind = bare_mind(model=model, system=system, ledger=ledger, world_requests=parse_request,
                         answer_world_request=lambda text: "Remembered.")
        poet = Personality.build({"id": "poet", "name": "Poet", "permissions": ["converse"]})
        mind.personalities.add(poet)
        mind.select_persona("terminal", "poet")
        self.assertFalse(mind.handle("what is the cpu load").ok)
        self.assertFalse(mind.handle("why is my computer slow").ok)
        self.assertFalse(mind.handle("shut down").ok)
        self.assertIsNone(mind.handle("shut down").proposal)
        self.assertFalse(mind.handle("remember that I prefer tea").ok)
        self.assertEqual((system.port.readings, system.port.analyses), ([], []))
        mind.handle("tell me about my sister, what do you know about her?")
        self.assertNotIn("Mira", model.all_text(), "a personality without memory.user was given the ledger")
        engine = mind.companion("poet")
        self.assertIsNone(engine.memory.ledger if hasattr(engine, "memory") else None)

    def test_the_companion_is_given_what_the_person_asked_it_to_remember(self):
        ledger = ledger_in_tmp()
        ledger.remember("the person's sister is called Mira", kind="memory")
        model = FakeModel("Mira, yes.")
        mind = bare_mind(model=model, ledger=ledger)
        mind.handle("tell me about my sister, what do you know about her?")
        self.assertIn("Mira", model.all_text())

    # -- conversation memory ------------------------------------------------------
    def test_conversations_are_not_kept_unless_asked(self):
        path = Path(tempfile.mkdtemp()) / "memory.db"
        mind = bare_mind(model=FakeModel("I see."), store=MemoryStore(path))
        mind.handle("I have been thinking about moving to the coast next year")
        self.assertEqual(mind.memory.namespaces("conversation/"), [])

    def test_kept_conversations_resume_after_a_restart_each_personality_its_own(self):
        path = Path(tempfile.mkdtemp()) / "memory.db"
        first = bare_mind(model=FakeModel("The coast sounds good."), store=MemoryStore(path),
                          keep_conversations=True)
        first.handle("I have been thinking about moving to the coast next year")
        first.handle("my api key is sk-abcdefghijklmnopqrstuvwxyz012345 by the way, remember it well")
        first.handle("my chest hurts")
        first.close()

        model = FakeModel("Yes — the coast.")
        second = bare_mind(model=model, store=MemoryStore(path), keep_conversations=True)
        second.handle("so what do you think I should do about it, honestly?")
        self.assertIn("moving to the coast", model.everything())
        kept = " ".join(r.text for r in second.memory.view(MIND).recent("conversation/companion", limit=50))
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz012345", kept, "a secret was written to disk")
        self.assertNotIn("chest", kept, "a sensitive turn was kept")

        second.select_persona("terminal", "technician")
        other = FakeModel("Understood.")
        second.models.use(other)
        second._companions.clear()
        second.handle("please tell me what we were discussing a moment ago, would you?")
        self.assertNotIn("coast", other.everything(), "one personality resumed another's conversation")
        second.close()

    def test_starting_over_clears_what_was_kept(self):
        mind = bare_mind(model=FakeModel("Fine."), keep_conversations=True)
        mind.handle("I have been thinking about moving to the coast next year")
        self.assertEqual(mind.memory.view(MIND).count("conversation/companion"), 1)
        mind.handle("start over")
        self.assertEqual(mind.memory.view(MIND).count("conversation/companion"), 0)

    # -- the rest -------------------------------------------------------------------
    def test_secrets_typed_in_conversation_are_redacted_before_the_model(self):
        model = FakeModel("I would not share that.")
        mind = bare_mind(model=model)
        mind.handle("is it bad that my password=CorrectHorse9Battery is on a sticky note, do you think?")
        self.assertNotIn("CorrectHorse9Battery", model.all_text())
        self.assertGreaterEqual(mind.models.usage()["os"]["redactions"], 1)

    def test_handle_can_be_awaited_and_turns_run_side_by_side(self):
        model = FakeModel("ok", delay=0.01)
        mind = bare_mind(model=model, system=fake_system())

        async def many():
            return await asyncio.gather(
                mind.ahandle("what is the cpu load", "api"),
                mind.ahandle("I had a strange dream about the sea last night", "voice"),
                mind.ahandle("talk to elara", "game"))

        system_reply, talk, game = asyncio.run(many())
        self.assertEqual((system_reply.route, talk.route, game.route),
                         (env.SYSTEM_QUERY, env.CONVERSATION, env.GAME))

    def test_environment_awareness_is_data_and_costs_nothing(self):
        model = FakeModel()
        system = fake_system()
        mind = bare_mind(model=model, system=system)
        seen = mind.environment("terminal")
        self.assertEqual((seen["persona"], seen["system"], seen["talking_to"]), ("companion", True, None))
        self.assertTrue(seen["model"]["available"])
        self.assertFalse(seen["cdos"]["linked"])
        self.assertIn("harbour", seen["worlds"])
        self.assertTrue(any(label == "MODELS LOADED" for label, _ in mind.status_rows()))
        self.assertEqual((model.calls, system.port.readings), ([], []))

    def test_a_response_is_structured_data(self):
        response = bare_mind(system=fake_system()).handle("shut down")
        data = response.to_dict()
        self.assertEqual((data["route"], data["mode"], data["proposal"]["target"]),
                         ("system_action", "SYSTEM", "system.shutdown"))
        import json
        json.dumps(data)


# ===========================================================================
# 6. Built from the shell's configuration, over the real machine
# ===========================================================================
class TestBuiltFromTheShell(unittest.TestCase):
    def test_building_costs_no_model_call_no_reading_and_no_database(self):
        server = FakeOllama()
        try:
            config = temp_config()
            config.values["companion"]["host"] = server.host
            mind = build_mind(config)
            mind.status_rows()
            self.assertEqual((server.tags_hits, server.payloads), (0, []))
            self.assertFalse(Path(config.values["mind"]["memory"]).exists())
            mind.close()
        finally:
            server.close()

    def test_real_readings_through_the_real_capability_layer(self):
        mind = shell().earth_mind()
        load = mind.handle("what is the cpu load", "api")
        self.assertTrue(load.ok, load.text)
        self.assertRegex(load.text, r"\d+%")
        top = mind.handle("what is using the most memory", "terminal")
        self.assertIn("PROGRAM", top.text)
        said = mind.handle("what is using the most memory", "voice")
        self.assertNotIn("PROGRAM", said.text, "a table was spoken")

    def test_the_real_shell_refuses_to_take_an_effectful_reading(self):
        ok, why = shell().answer_reading("system.shutdown")
        self.assertFalse(ok)
        self.assertIn("not a reading", why)
        ok, why = shell().answer_reading("nonesuch.thing")
        self.assertFalse(ok)

    def test_cdos_answers_a_sum_with_no_model(self):
        mind = shell().earth_mind()
        if mind.link is None or not mind.link.available:
            self.skipTest("cdos-computer is not in this checkout")
        response = mind.handle("what is 12 times 9", "api")
        self.assertIn("108", response.text)
        self.assertEqual((response.source, response.model_calls), ("computer", 0))
        self.assertIn(env.CDOS, response.subsystems)

    def test_disabled_in_config_there_is_no_mind_and_the_shell_is_whole(self):
        config = temp_config(enabled=False)
        quiet = Shell(config)
        self.assertIsNone(quiet.earth_mind())
        self.assertEqual(quiet.mind_error, "")
        ok, text = quiet.answer_analysis("top_memory")
        self.assertTrue(ok, text)


if __name__ == "__main__":
    unittest.main()
