import plistlib,sys
from pathlib import Path
bundle=Path(sys.argv[1])
info={'CFBundleName':'出运工作台','CFBundleDisplayName':'出运工作台','CFBundleIdentifier':'local.shu.shipment-workbench','CFBundleExecutable':'Workbench','CFBundlePackageType':'APPL','CFBundleShortVersionString':'3.0.0','CFBundleVersion':'30','NSHighResolutionCapable':True,'LSMinimumSystemVersion':'12.0','CFBundleIconFile':'AppIcon','NSAppTransportSecurity':{'NSAllowsLocalNetworking':True},'NSSupportsAutomaticTermination':False,'NSSupportsSuddenTermination':False}
(bundle/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
