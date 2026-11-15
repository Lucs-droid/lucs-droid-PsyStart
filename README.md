# PsiStart

Ghid independent pentru psihologi la început de drum: dosarul de ALP, cabinetul, legislația, registrele și taxele, organizate pe pași, cu link către sursa oficială.

> Proiect personal. Nu este afiliat Colegiului Psihologilor din România și nu oferă consultanță juridică. Sursa oficială are întotdeauna prioritate.

## Cum e construit

| Fișier | Ce face |
|---|---|
| `index.html` | Pagina. Citește datele din `data/`. |
| `data/resources.json` | **Fișele.** Aici adaugi, corectezi sau marchezi ca verificat un document. |
| `data/status.json` | Scris de robot: ultima verificare și starea fiecărui link. |
| `data/noutati.json` | Scris de robot: documente noi apărute pe copsi.ro. |
| `monitor.py` | Robotul de verificare. |
| `.github/workflows/monitor.yml` | Rulează robotul zilnic (și manual, din Actions → Run workflow). |

## Ce e automat și ce nu

- **Automat:** verificarea zilnică a linkurilor, detectarea modificărilor în documente și a documentelor noi pe copsi.ro.
- **Cu aprobarea ta:** când un document se schimbă, robotul deschide un **Issue** cu eticheta `sursa-modificata`. Tu verifici, actualizezi fișa în `data/resources.json` (inclusiv data din `verified`) și închizi issue-ul.
- **Opțional:** dacă adaugi secretul `ANTHROPIC_API_KEY` (Settings → Secrets and variables → Actions), issue-ul include și un rezumat al modificării.

## Stări ale fișelor

`oficial` · `de-confirmat` · `proiect` · `in-lucru` · `lipsa`. O fișă trece la `oficial` doar după ce a fost verificată pe sursa oficială.
