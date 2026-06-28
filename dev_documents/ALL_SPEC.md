# TODO Application

## Overview
A simple CLI-based TODO application that allows users to manage tasks with title, description, priority, and due date.

## Functional Requirements

### Cycle 1: Core CRUD + CLI Interface
- [x] Create TODO items with title, description, priority (low/medium/high), and due date
- [x] List all TODO items with their status
- [x] Mark a TODO item as complete
- [x] Delete a TODO item
- [x] CLI interface using `argparse` or `typer` with commands: `add`, `list`, `complete`, `delete`
- [x] Store data in a local JSON file (`~/.todo/tasks.json`)

### Cycle 2: Search, Filter & Persistence Enhancement
- [x] Filter TODOs by status (completed/pending)
- [x] Filter TODOs by priority (low/medium/high)
- [x] Search TODOs by keyword in title/description
- [x] Sort TODOs by due date or priority
- [x] Edit existing TODO items (update title, description, priority, due date)
- [x] Data validation: due date format (YYYY-MM-DD), priority must be one of low/medium/high

## Technical Requirements

### Project Structure
```
todo-app/
├── todo/
│   ├── __init__.py
│   ├── cli.py          # CLI entry point
│   ├── models.py       # Data models (TodoItem, Priority)
│   ├── storage.py      # JSON file storage
│   └── utils.py        # Validation helpers
├── tests/
│   ├── __init__.py
│   ├── test_models.py
│   ├── test_storage.py
│   └── test_cli.py
├── pyproject.toml
└── README.md
```

### Dependencies
- Python >= 3.12
- `typer` for CLI
- `pydantic` for data models
- `pytest` for testing

### Quality Gates
- All tests must pass (pytest)
- Type hints required on all public functions
- Error handling for file I/O and invalid input
- Use `pathlib` for file path operations
