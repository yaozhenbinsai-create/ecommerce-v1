"""Bound application runtime logs without deleting job evidence or business assets."""
from pathlib import Path


def configuration(data: Path):
    directory = Path(data).resolve() / 'logs'
    directory.mkdir(parents=True, exist_ok=True)
    return {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {'runtime': {'format': '%(asctime)s %(levelname)s %(name)s %(message)s'}},
        'handlers': {'runtime': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(directory / 'runtime.log'),
            'maxBytes': 5 * 1024 * 1024,
            'backupCount': 4,
            'encoding': 'utf-8',
            'formatter': 'runtime',
        }},
        'root': {'handlers': ['runtime'], 'level': 'WARNING'},
        'loggers': {
            'uvicorn': {'handlers': ['runtime'], 'level': 'INFO', 'propagate': False},
            'uvicorn.error': {'handlers': [], 'level': 'INFO', 'propagate': True},
            'uvicorn.access': {'handlers': [], 'level': 'WARNING', 'propagate': False},
            'studio': {'handlers': ['runtime'], 'level': 'INFO', 'propagate': False},
        },
    }
