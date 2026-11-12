from pathlib import Path
from typing import Any, Dict, Tuple, Type

from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from core.config.yaml_loader import load_config_file

SETTINGS_FILE_NAME = "core.config"
SUPPORTED_CONFIG_FILES: Tuple[Tuple[str, str], ...] = (
    ("yaml", "yaml"),
    ("yml", "yaml"),
)


class SettingsFileSource(PydanticBaseSettingsSource):
    """
    A settings source that loads variables from a YAML file
    at the project's root.
    """

    data: dict

    def __init__(self, settings_cls: Type[BaseSettings]):
        super().__init__(settings_cls)

        self.data = self._load_config_file()

    def _load_config_file(self) -> dict:
        from dotenv import load_dotenv

        raw_env = self.config.get("env_file")
        env_files = list(raw_env) if isinstance(raw_env, (list, tuple)) else [raw_env]
        encoding = self.config.get("env_file_encoding")

        # Load the env vars explicitly as pydantic reads env vars
        # locally without loading them into the system
        for f in env_files:
            if f is not None:
                load_dotenv(str(f))

        # Find user's defined config file among the supported ones
        first_env = env_files[0] if env_files else Path.cwd()
        base_folder = first_env.parent if isinstance(first_env, Path) else Path(str(first_env)).parent
        for [ext, _] in SUPPORTED_CONFIG_FILES:
            config_file = base_folder / f"{SETTINGS_FILE_NAME}.{ext}"
            if config_file.exists():
                break
        else:
            # Any config file has been found
            return dict()

        return load_config_file(config_file, encoding)

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> Tuple[Any, str, bool]:
        field_value = self.data.get(field_name)
        return field_value, field_name, False

    def prepare_field_value(
        self, field_name: str, field: FieldInfo, value: Any, value_is_complex: bool
    ) -> Any:
        return value

    def __call__(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {}

        for field_name, field in self.settings_cls.model_fields.items():
            field_value, field_key, value_is_complex = self.get_field_value(
                field, field_name
            )
            field_value = self.prepare_field_value(
                field_name, field, field_value, value_is_complex
            )
            if field_value is not None:
                d[field_key] = field_value

        return d
