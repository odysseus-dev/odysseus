<p align="center">
  <img src="docs/odysseus-wordmark.png" alt="Odysseus" width="238">
</p>

<p align="center">
  Самостоятельно размещаемая ИИ-среда для чата, агентов, исследований, документов, почты, заметок, календаря и работы с локальными моделями.
</p>

<p align="center">
  <a href="#быстрое-начало">Быстрое начало</a> ·
  <a href="docs/ru/setup.md">Руководство по настройке</a> ·
  <a href="CONTRIBUTING.md">Участие в проекте</a> ·
  <a href="ROADMAP.md">Дорожная карта</a>
</p>

<p align="center">
  <a href="https://repology.org/project/odysseus-ai/versions"><img src="https://repology.org/badge/vertical-allrepos/odysseus-ai.svg" alt="Packaging status"></a>
</p>

<p align="center">
  <img src="docs/odysseus-browser.jpg" alt="Интерфейс Odysseus">
</p>

---

## Быстрое начало

> `dev` — ветка по умолчанию, в первую очередь получает новые изменения. Используйте [`main`](https://github.com/pewdiepie-archdaemon/odysseus/tree/main), если хотите более отсортированную ветку.

```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
cp .env.example .env
docker compose up -d --build
```

Откройте `http://localhost:7000`, когда контейнеры будут работать. Первый пароль администратора выводится в `docker compose logs odysseus`.

Нативная установка, примечания по GPU, инструкции для Windows/macOS, HTTPS и конфигурация описаны в [руководстве по настройке](docs/ru/setup.md).

## Возможности

- **Чат + Агенты** — локальные/API модели, инструменты, MCP, файлы, командная оболочка, навыки и память.
- **Книга рецептов** — аппаратно-зависимые рекомендации моделей, загрузка и обслуживание.
- **Глубокое исследование** — многошаговое веб-исследование с чтением источников и генерацией отчётов.
- **Сравнение** — слепое поэлементное тестирование и синтез моделей.
- **Документы** — редактор с приоритетом на письмо, с ИИ-редактированием, подсказками, Markdown, HTML, CSV и подсветкой синтаксиса.
- **Почта** — почтовый ящик IMAP/SMTP с сортировкой, тегами, саммари, напоминаниями и черновиками ответов.
- **Заметки, задачи + календарь** — напоминания, списки дел, запланированные задачи агентов и синхронизация CalDAV.
- **Дополнительно** — галерея/редактор изображений, темы, загрузки, веб-поиск, пресеты, сессии и 2FA.

## Демо

Полный интерактивный тур по интерфейсу с наведением для воспроизведения находится на главной странице: [`docs/index.html`](docs/index.html).

## Участие в проекте

Мы приветствует лучшие точки входа — тестирование свежей установки, ошибки настройки провайдеров, доработка мобильной версии/редактора, документация и небольшие целевые рефакторы. См. [CONTRIBUTING.md](CONTRIBUTING.md) и [ROADMAP.md](ROADMAP.md).

## Безопасность

Odysseus — самостоятельная среда с мощными лостными инструментами. Сохраняйте включённую аутентификацию, не храните конфиденциальные данные в Git и не публикуйте порты моделей/сервисов в открытом доступе. Подробности развёртывания в [руководстве по настройке](docs/ru/setup.md#security-notes).

## История звёзд

<a href="https://www.star-history.com/?repos=pewdiepie-archdaemon%2Fodysseus&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&legend=top-left" />
 </picture>
</a>

## Лицензия

AGPL-3.0-or-later — см. [LICENSE](LICENSE) и [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).
