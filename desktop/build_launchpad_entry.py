"""Build a local Applications entry without moving or re-signing the workbench."""
from pathlib import Path
import hashlib
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
target = ROOT / '出运工作台.app'
assert (ROOT / 'app.py').is_file() and target.is_dir()
bundle = ROOT / 'data' / 'local-launcher-build' / '出运工作台.app'
contents = bundle / 'Contents'
for folder in ('MacOS', 'Resources'):
    (contents / folder).mkdir(parents=True, exist_ok=True)

def target_hashes():
    return {str(p.relative_to(target)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in target.rglob('*') if p.is_file()}

before = target_hashes()
subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-fblocks', '-O2',
                '-mmacosx-version-min=12.0', '-framework', 'Cocoa',
                str(ROOT / 'desktop' / 'LaunchpadEntry.m'), '-o',
                str(contents / 'MacOS' / 'LaunchpadEntry')], check=True)
metadata = dict(CFBundleName='出运工作台', CFBundleDisplayName='出运工作台',
                CFBundleIdentifier='local.shu.shipment-workbench.launchpad',
                CFBundleExecutable='LaunchpadEntry', CFBundlePackageType='APPL',
                CFBundleIconFile='AppIcon', CFBundleVersion='1',
                CFBundleShortVersionString='1.0', LSMinimumSystemVersion='12.0',
                LSUIElement=True, ShipmentWorkbenchTarget=str(target))
with (contents / 'Info.plist').open('wb') as stream:
    plistlib.dump(metadata, stream)
shutil.copy2(ROOT / 'desktop' / 'AppIcon.icns', contents / 'Resources' / 'AppIcon.icns')
subprocess.run(['codesign', '--force', '--sign', '-', str(bundle)], check=True)
subprocess.run(['codesign', '--verify', '--strict', str(bundle)], check=True)
assert target_hashes() == before, 'The original workbench must remain unchanged'
print(bundle)
