try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib
import tomli_w

__all__ = ["tomllib", "tomli_w"]
