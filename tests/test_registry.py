import sys
from unittest.mock import MagicMock, patch

# Aggressively mock EVERYTHING BEFORE any imports
sys.modules['winreg'] = MagicMock()
sys.modules['comtypes'] = MagicMock()
sys.modules['comtypes.client'] = MagicMock()
sys.modules['win32gui'] = MagicMock()
sys.modules['win32con'] = MagicMock()
sys.modules['win32process'] = MagicMock()
sys.modules['pywintypes'] = MagicMock()
sys.modules['win32com'] = MagicMock()
sys.modules['win32com.shell'] = MagicMock()
sys.modules['psutil'] = MagicMock()
sys.modules['PIL'] = MagicMock()
sys.modules['PIL.Image'] = MagicMock()
sys.modules['PIL.ImageFont'] = MagicMock()
sys.modules['PIL.ImageDraw'] = MagicMock()
sys.modules['tabulate'] = MagicMock()
sys.modules['markdownify'] = MagicMock()
sys.modules['fuzzywuzzy'] = MagicMock()
sys.modules['requests'] = MagicMock()
sys.modules['windows_mcp.vdm.core'] = MagicMock()
sys.modules['windows_mcp.uia'] = MagicMock()
sys.modules['windows_mcp.tree.service'] = MagicMock()
sys.modules['windows_mcp.desktop.screenshot'] = MagicMock()

# Mock sys.getwindowsversion
if not hasattr(sys, 'getwindowsversion'):
    mock_version = MagicMock()
    mock_version.major = 10
    mock_version.build = 19041
    sys.getwindowsversion = MagicMock(return_value=mock_version)

# Mock ctypes.windll
import ctypes
if not hasattr(ctypes, 'windll'):
    ctypes.windll = MagicMock()

if not hasattr(ctypes, 'HRESULT'):
    ctypes.HRESULT = ctypes.c_long

import pytest

# Now import Desktop
from windows_mcp.desktop.service import Desktop
import winreg # This should now be the mock

@pytest.fixture
def desktop():
    with patch.object(Desktop, '__init__', lambda self: None):
        d = Desktop()
        d._parse_reg_path = MagicMock()
        return d

class TestRegistryGet:
    def test_success(self, desktop):
        desktop._parse_reg_path.return_value = (1, "Software\\Test")
        winreg.OpenKey.return_value.__enter__.return_value = "key_handle"
        winreg.QueryValueEx.return_value = ("42", 1) # 1 = REG_SZ

        result = desktop.registry_get(path="HKCU:\\Software\\Test", name="MyValue")

        assert 'MyValue' in result
        assert '42' in result
        winreg.OpenKey.assert_called_with(1, "Software\\Test")
        winreg.QueryValueEx.assert_called_with("key_handle", "MyValue")

    def test_failure(self, desktop):
        desktop._parse_reg_path.return_value = (1, "Software\\Test")
        winreg.OpenKey.side_effect = Exception("Key not found")
        result = desktop.registry_get(path="HKCU:\\Software\\Test", name="Missing")
        assert 'Error reading registry' in result
        assert 'Key not found' in result
        winreg.OpenKey.side_effect = None

class TestRegistrySet:
    def test_success(self, desktop):
        desktop._parse_reg_path.return_value = (1, "Software\\Test")
        winreg.HKEY_CURRENT_USER = 1
        winreg.REG_SZ = 1
        winreg.KEY_SET_VALUE = 2
        winreg.CreateKeyEx.return_value.__enter__.return_value = "key_handle"

        result = desktop.registry_set(path="HKCU:\\Software\\Test", name="MyKey", value="hello")

        assert 'set to' in result
        assert 'hello' in result
        winreg.CreateKeyEx.assert_called_with(1, "Software\\Test", 0, 2)
        winreg.SetValueEx.assert_called_with("key_handle", "MyKey", 0, 1, "hello")

    def test_dword_conversion(self, desktop):
        desktop._parse_reg_path.return_value = (1, "Software\\Test")
        winreg.REG_DWORD = 4
        result = desktop.registry_set(path="HKCU:\\Test", name="Val", value="123", reg_type="DWord")
        assert 'set to "123"' in result
        winreg.SetValueEx.assert_called_with("key_handle", "Val", 0, 4, 123)

    def test_invalid_type(self, desktop):
        result = desktop.registry_set(path="HKCU:\\Test", name="Key", value="val", reg_type="Invalid")
        assert 'Error: invalid registry type' in result

class TestRegistryDelete:
    def test_delete_value(self, desktop):
        desktop._parse_reg_path.return_value = (1, "Software\\Test")
        winreg.OpenKey.return_value.__enter__.return_value = "key_handle"
        result = desktop.registry_delete(path="HKCU:\\Software\\Test", name="MyValue")
        assert 'deleted' in result
        assert '"MyValue"' in result
        winreg.DeleteValue.assert_called_with("key_handle", "MyValue")

    def test_delete_key(self, desktop):
        desktop._parse_reg_path.return_value = (1, "Software\\Test")
        # Mock recursion: EnumKey returns a subkey then raises OSError
        # We need to make sure EnumKey works for both the parent key and the child key
        # First call for parent key returns "SubKey"
        # Second call for parent key returns OSError (no more subkeys)
        # First call for child key returns OSError (no subkeys)
        winreg.EnumKey.side_effect = ["SubKey", OSError(), OSError()]
        winreg.OpenKey.return_value.__enter__.return_value = "key_handle"
        winreg.KEY_ALL_ACCESS = 1

        result = desktop.registry_delete(path="HKCU:\\Software\\Test", name=None)
        assert 'deleted' in result
        # Verify DeleteKey was called for subkey and the key itself
        assert winreg.DeleteKey.call_count >= 2
        winreg.EnumKey.side_effect = None

class TestRegistryList:
    def test_success(self, desktop):
        desktop._parse_reg_path.return_value = (1, "Software\\Test")
        winreg.EnumValue.side_effect = [("Val1", "Data1", 1), OSError()]
        winreg.EnumKey.side_effect = ["Sub1", OSError()]
        winreg.KEY_READ = 1

        result = desktop.registry_list(path="HKCU:\\Software\\Test")
        assert 'Val1' in result
        assert 'Sub1' in result
        winreg.EnumValue.side_effect = None
        winreg.EnumKey.side_effect = None
