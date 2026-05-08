# Plan na następną sesję

**Stan na koniec sesji 2026-05-08 (~23:30 CEST):**
- ✅ SPO v3 rich-JSON pipeline (v1 cram + v2 split, oba z meta+sponsored steps)
- ✅ ProcessPoolExecutor w streaming_loader (39× speedup cache warmup)
- ✅ Cache portability Spark→prod udokumentowany (`PLANS/production_deployment.md`)
- ⏳ Sequential bench v1+v2 conc=8 leci (`final_results/2026-05-08_23-22-24__spo_v1_v2_seq_v3_seq/`)
- ETA do końca: niedziela 11.05 rano

**Repo:** https://github.com/romek-rozen/data-extraction-vllm-gemma4-26b
**Sesja podsumowanie:** [`SESSIONS_SUMMARY/2026-05-08_session_close.md`](../SESSIONS_SUMMARY/2026-05-08_session_close.md)

---

## Roadmap kolejnych sesji

| # | Plan | Plik | Czas |
|---|---|---|---|
| 1 | **Dashboard SPO v3 (rich JSON)** | [`PLANS/spo_dashboard_v3_plan.md`](spo_dashboard_v3_plan.md) | 4-6h |
| 2 | Predicate refinement (closed enum dla v4) | [`PLANS/spo_predicate_refinement_plan.md`](spo_predicate_refinement_plan.md) | 6-8h |
| 3 | Atrybuty SPO → entity.metadata + walidacja | (TBD) | 3-4h |
| 4 | Production deployment plan + verifiers | [`PLANS/production_deployment.md`](production_deployment.md) | 4-6h |
| 5 | Migracja na RTX 6000 Pro (RunPod setup, smoke 1000) | [`PLANS/rtx_pro_6000_optimization.md`](rtx_pro_6000_optimization.md) | 6-8h |
| 6 | Production run 26M URL launch | (start + monitor) | bieg |

**Total przed prod runem: ~25-35h pracy.**

---

## Krok 0 — Onboarding (3 min)

```bash
cd /home/spark001/Spark-testy/mateusz-g-two-step-vllm
git pull
git log --oneline -10
cat SESSIONS_SUMMARY/2026-05-08_session_close.md | head -100
cat FILES.md | head -80                    # mapa repo
```

**Czytaj w tej kolejności:**
1. `CLAUDE.md` — wskazówki ogólne
2. `FILES.md` — co gdzie leży
3. `DECISIONS.md` (D21-D28) — najnowsze decyzje SPO
4. `PLANS/spo_dashboard_v3_plan.md` — co robić w tej sesji

---

## Krok 1 — Sprawdź stan bench

```bash
# Czy sequential master skończył?
tmux ls 2>&1 | grep spo_seq && echo "STILL RUNNING" || echo "DONE"

# Sprawdź wyniki
MD=$(ls -dt final_results/*__spo_v1_v2_seq_v3_seq | head -1)
echo "MASTER: $MD"
ls "$MD/"
cat "$MD/run_log.txt" | tail -20
[ -f "$MD/comparison_report.md" ] && head -50 "$MD/comparison_report.md"

# Per-pipeline counts
V1=$(ls -dt final_results/*__spo_v1_v3_seq | head -1)
V2=$(ls -dt final_results/*__spo_v2_v3_seq | head -1)
echo "v1: $V1 — $(wc -l < $V1/final.jsonl 2>/dev/null) records"
echo "v2: $V2 — $(wc -l < $V2/final.jsonl 2>/dev/null) records"
```

Jeśli sequential JESZCZE leci — daj mu skończyć. Jeśli skończył — dashboard time.

---

## Krok 2 — Sprawdź vLLM

```bash
docker ps --filter name=vllm-gemma4
curl -sS http://localhost:8001/v1/models | python3 -m json.tool
```

Jeśli down — `bash scripts/start_vllm.sh`.

---

## Krok 3 — Pracuj według planu

Plan tej sesji w [`PLANS/spo_dashboard_v3_plan.md`](spo_dashboard_v3_plan.md).
Po skończeniu dashboardu i harvest predicate distribution → kolejna sesja
[`PLANS/spo_predicate_refinement_plan.md`](spo_predicate_refinement_plan.md).
