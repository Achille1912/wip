import importlib.machinery, importlib.util, os, tempfile

_l = importlib.machinery.SourceFileLoader("pv", os.path.join(os.path.dirname(__file__), "pipeline-viewer"))
pv = importlib.util.module_from_spec(importlib.util.spec_from_loader("pv", _l)); _l.exec_module(pv)


def test_ancore_prove_e_copertura():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "a.py"), "w").write("from b import g\ndef f():\n    g()\n")
        open(os.path.join(d, "b.py"), "w").write("def g():\n    pass\ndef orfana():\n    pass\n")
        open(os.path.join(d, "c.py"), "w").write("def n1(): pass\ndef n2(): pass\ndef w(x, f): return f\n"
                                                  "g.add_node('one', w('one', n1)); g.add_node('two', n2); g.add_edge('one', 'two')\n")
        spec = {"blocchi": [{"id": "A", "testo": "A", "tipo": "step", "ancore": ["a.py:f"]},
                            {"id": "N1", "testo": "N1", "tipo": "step", "ancore": ["c.py:n1"]},
                            {"id": "N2", "testo": "N2", "tipo": "step", "ancore": ["c.py:n2"]},
                            {"id": "B", "testo": "B", "tipo": "step", "ancore": ["b.py:g"]},
                            {"id": "X", "testo": "X", "tipo": "step", "ancore": ["b.py:non_esiste"]}],
                "archi": [["A", "B"], ["B", "A"], ["N1", "N2"], ["N2", "N1"]]}
        r = pv.controlla(spec, pv.estrai(d))
        assert r["archi"]["N1>N2"] and not r["archi"]["N2>N1"]  # add_edge('one','two') e' una prova, al contrario no
        assert r["stati"]["A"]["stato"] == "ok" and r["stati"]["X"]["stato"] == "rotto"
        assert r["archi"]["A>B"].startswith("usa g") and not r["archi"]["B>A"]  # f chiama g, g non chiama f
        nodi = [{"id": "A", "testo": "A|sotto", "tipo": "step", "ancore": ["src/x/a.py:f"]}, {"id": "B", "testo": "B", "tipo": "step"},
                {"id": "C", "testo": "C", "tipo": "store", "descrizione": "isolato"}]
        ext = [{"da": "C", "altro": "Z", "dir": "out", "et": "usa k", "pr": "usa k", "titolo": "Zeta", "d": "blocco Z"}]
        v, m = pv.vista("", "t", nodi, [["A", "B", "x"], ["B", "A"]], None, {"B": [500, 40]}, [{"id": "g", "titolo": "G", "nodi": ["A", "B"]}], ext)
        assert v["pos"] == {"B": [500, 40]} and v["archi"][0]["label"] == "x" and v["archi"][1]["da"]  # posizione a mano e arco di ciclo
        assert [n["gruppo"] for n in v["nodi"][:2]] == ["#g", "#g"] and v["nodi"][0]["sotto"] == "sotto" and v["nodi"][0]["h"] > 66
        assert {n["id"] for n in v["nodi"]} == {"A", "B", "C", "@Z"} and m["@Z"]["apri"] == "Z"  # C ha una relazione esterna: niente nota
        v2, _ = pv.vista("", "t", nodi[2:], [], None, None, None, None)
        assert [n["tipo"] for n in v2["nodi"]] == ["store", "nota"] and v2["archi"][0]["k"] == "nota"  # senza nulla: nota UML
        assert sorted(r["copertura"]["scoperti"]) == ["b.py:orfana", "c.py:w"]
        # il codice cambia dopo la verifica -> da riverificare
        E = pv.estrai(d); sig = {s: E["sim"][s]["hash"] for s in r["sids"]["B"]}
        open(os.path.join(d, "b.py"), "w").write("def g():\n    return 1\ndef orfana():\n    pass\n")
        assert pv.controlla(spec, pv.estrai(d), sig)["stati"]["B"]["stato"] == "da riverificare"
