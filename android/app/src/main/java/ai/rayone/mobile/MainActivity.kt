package ai.rayone.mobile

import android.os.Bundle
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread
import org.json.JSONObject

class MainActivity : AppCompatActivity() {
    private val baseUrl = "http://10.0.2.2:8000"
    private var token = ""
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(24,24,24,24) }
        val password = EditText(this).apply { hint="Admin password"; inputType=0x81 }
        val message = EditText(this).apply { hint="Ask RAYONE"; minLines=3 }
        val output = TextView(this).apply { textSize=15f }
        val login = Button(this).apply { text="Login" }
        val run = Button(this).apply { text="Run"; isEnabled=false }
        root.addView(password); root.addView(login); root.addView(message); root.addView(run); root.addView(output)
        setContentView(root)
        login.setOnClickListener {
            thread {
                try {
                    val body=JSONObject().put("password",password.text.toString()).toString()
                    val r=request("/api/auth/login","POST",body)
                    token=JSONObject(r).getString("token")
                    runOnUiThread { output.text="Logged in"; run.isEnabled=true }
                } catch(e:Exception){ runOnUiThread { output.text=e.message } }
            }
        }
        run.setOnClickListener {
            thread {
                try {
                    val body=JSONObject().put("message",message.text.toString()).toString()
                    val r=request("/api/v2/assistant/chat","POST",body)
                    runOnUiThread { output.text=JSONObject(r).optString("answer",r) }
                } catch(e:Exception){ runOnUiThread { output.text=e.message } }
            }
        }
    }
    private fun request(path:String, method:String, body:String):String {
        val c=URL(baseUrl+path).openConnection() as HttpURLConnection
        c.requestMethod=method; c.setRequestProperty("Content-Type","application/json")
        if(token.isNotEmpty()) c.setRequestProperty("Authorization","Bearer $token")
        if(method!="GET"){ c.doOutput=true; c.outputStream.use{it.write(body.toByteArray())} }
        val stream=if(c.responseCode<400)c.inputStream else c.errorStream
        return stream.bufferedReader().use{it.readText()}
    }
}
