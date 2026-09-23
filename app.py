from pathlib import Path
import os
import streamlit as st
from shipping.services import Workbench
from shipping.ui.pages import run

st.set_page_config(page_title='出运单证工作台',page_icon='▧',layout='wide',initial_sidebar_state='auto')
root=Path(os.getenv('SHIPPING_DATA_DIR',str(Path(__file__).parent/'data')))
run(Workbench(root))
