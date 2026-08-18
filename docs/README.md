# Документация проекта

## Навигация

### Техническое задание
| Документ | Что внутри |
|---|---|
| [spec/TZ_Utrenniy_Radar.md](spec/TZ_Utrenniy_Radar.md) | **Актуальное ТЗ** ежедневного радара (v1.1) |
| [spec/TZ_Model_Portfolio.md](spec/TZ_Model_Portfolio.md) | **Актуальное ТЗ** модельного портфеля (v1.1) |
| [spec/CHANGELOG.md](spec/CHANGELOG.md) | Что и почему менялось между версиями |
| [spec/original/](spec/original/) | Исходное ТЗ v1.0 — неизменяемый baseline |

### Промты для Cowork Scheduled Tasks
| Документ | Задача |
|---|---|
| [prompts/daily_task_prompt.md](prompts/daily_task_prompt.md) | Ежедневный радар (будни, утро) |
| [prompts/portfolio_task_prompt.md](prompts/portfolio_task_prompt.md) | Модельный портфель (еженедельно + по запросу) |

### База знаний
| Документ | Что внутри |
|---|---|
| [knowledge/investor_profile.md](knowledge/investor_profile.md) | ⏳ Анкета инвестпрофиля — **блокирует старт портфеля** |
| [knowledge/methodology.md](knowledge/methodology.md) | Расчёт дельт, оценка риска 0–100, пороги флага пересмотра |
| [knowledge/data_schemas.md](knowledge/data_schemas.md) | Схемы всех файлов в `data/` |
| [knowledge/daily_summary_template.md](knowledge/daily_summary_template.md) | Шаблон дневной сводки |
| [knowledge/data_sources.md](knowledge/data_sources.md) | Источники, приоритеты, подвохи |
| [knowledge/network_and_data_access.md](knowledge/network_and_data_access.md) | ⏳ Настройка сетевого доступа и MOEX ISS API |
| [knowledge/regulatory_notes.md](knowledge/regulatory_notes.md) | Ограничения для неквала + журнал сверок |
| [knowledge/portfolio_decisions_log.md](knowledge/portfolio_decisions_log.md) | Решения по портфелю и проверяемые гипотезы |
| [knowledge/ideas_backlog.md](knowledge/ideas_backlog.md) | Идеи и улучшения вне текущего ТЗ |
| [knowledge/research_log.md](knowledge/research_log.md) | Журнал исследований, включая отрицательные результаты |

### Решения (ADR)
| ADR | Решение |
|---|---|
| [ADR-0001](decisions/ADR-0001-storage-in-git.md) | Хранение истории и знаний в git |
| [ADR-0002](decisions/ADR-0002-html-instead-of-pdf.md) | HTML вместо PDF |
| [ADR-0003](decisions/ADR-0003-two-layer-history.md) | Двухуровневая история дневной аналитики |

### История взаимодействий
[sessions/](sessions/) — по файлу на сессию: что обсуждали, что решили, что изменилось.

---

## Как поддерживать документацию

Требование заказчика: все анализы, наработки, навыки, идеи и история исследований
живут здесь и **обновляются при каждом взаимодействии**. Практически это значит:

| Что произошло | Что обновить |
|---|---|
| Заказчик уточнил требование | ТЗ (поднять версию) + `spec/CHANGELOG.md` + `sessions/` |
| Принято архитектурное решение | Новый ADR + ссылка из ТЗ |
| Что-то выяснили (в т.ч. «не работает») | `knowledge/research_log.md` |
| Появилась идея вне ТЗ | `knowledge/ideas_backlog.md` |
| Уточнили метод расчёта | `knowledge/methodology.md` + запись в research_log |
| Изменился watchlist | `data/watchlist_config.json` + причина в `history` |
| Пересмотрен портфель | `data/portfolio_history.jsonl` + `knowledge/portfolio_decisions_log.md` |
| Прошла сверка правил для неквала | `knowledge/regulatory_notes.md` |
| Любое взаимодействие | Файл в `sessions/` |

Правила: ТЗ версионируется (v1.0 → v1.1 → …), baseline в `spec/original/` не
редактируется никогда, история в `data/*.jsonl` только дописывается, каждое
изменение — отдельный коммит с внятным сообщением.
