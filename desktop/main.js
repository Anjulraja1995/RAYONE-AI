const {app,BrowserWindow}=require("electron");
function create(){const w=new BrowserWindow({width:1440,height:900,webPreferences:{contextIsolation:true}});w.loadURL(process.env.RAYONE_URL||"http://127.0.0.1:8000");}
app.whenReady().then(create);app.on("window-all-closed",()=>{if(process.platform!=="darwin")app.quit()});
