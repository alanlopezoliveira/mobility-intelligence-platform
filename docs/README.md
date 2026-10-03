# Project documentation

| Document | Purpose |
|---|---|
| [Developer guide](developer-guide.md) | Install, prepare data, run the application and perform checks |
| [Provider onboarding](providers.md) | Adapter contract, CSV mappings, timezone rules and provider isolation |
| [Data and methods](rebuilt-project.md) | Current architecture, sources, data contracts and limitations |
| [Model comparison](model-comparison.md) | Generated comparison of candidates on the published evaluation population |
| [Licensing and attribution](legal-and-attribution.md) | Code license, data credits and reuse conditions |
| [Course submissions](entregas/README.md) | Submitted coursework; illustrations are in `assets/` |

The default application uses the 2022 reference workflow. The PostgreSQL explorer and older inventory evidence have separate scopes, described in the guides above.

Keep reusable explanations here. Generated audit reports belong in `data/audit/`; data and model artifacts remain local and excluded from Git. The model comparison is the one generated document retained here: its source is `data/rebuilt/2022/report.json`, and it can be refreshed without training using `python scripts/document_model_comparison.py`.
