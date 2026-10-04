package ai.rayone.mobile

import android.os.Bundle
import android.widget.*
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.content.Intent
import android.speech.tts.TextToSpeech
import java.util.Locale
import androidx.appcompat.app.AppCompatActivity
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread
import org.json.JSONObject

class MainActivity : AppCompatActivity() {
    private val baseUrl = "http://10.0.2.2:8000"
    private var token = ""
    private var tts: TextToSpeech? = null
    private var recognizer: SpeechRecognizer? = null
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(24,24,24,24) }
        val password = EditText(this).apply { hint="Admin password"; inputType=0x81 }
        val message = EditText(this).apply { hint="Ask RAYONE"; minLines=3 }
        val output = TextView(this).apply { textSize=15f }
        val login = Button(this).apply { text="Login" }
        val run = Button(this).apply { text="Run"; isEnabled=false }
        val voice=Button(this).apply { text="Voice Input" }
        root.addView(password); root.addView(login); root.addView(message); root.addView(voice); root.addView(run); root.addView(output)
        voice.setOnClickListener {
            val intent=RecognizerIntent().apply { action=RecognizerIntent.ACTION_RECOGNIZE_SPEECH; putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL,RecognizerIntent.LANGUAGE_MODEL_FREE_FORM); putExtra(RecognizerIntent.EXTRA_LANGUAGE,Locale.getDefault()) }
            recognizer?.setRecognitionListener(object: android.speech.RecognitionListener {
                override fun onResults(results: android.os.Bundle){ message.setText(results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull() ?: "") }
                override fun onError(error:Int) { output.text="Voice error: $error" }
                override fun onReadyForSpeech(p:android.os.Bundle?){}; override fun onBeginningOfSpeech(){}; override fun onRmsChanged(v:Float){}; override fun onBufferReceived(b:ByteArray?){}; override fun onEndOfSpeech(){}; override fun onPartialResults(b:android.os.Bundle?){}; override fun onEvent(t:Int,b:android.os.Bundle?){ }
            }); recognizer?.startListening(intent)
        }
        setContentView(root)
        tts=TextToSpeech(this){ if(it==TextToSpeech.SUCCESS) tts?.language=Locale.getDefault() }
        recognizer=SpeechRecognizer.createSpeechRecognizer(this)
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
                    runOnUiThread { val answer=JSONObject(r).optString("answer",r); output.text=answer; if(answer.isNotBlank()) tts?.speak(answer,TextToSpeech.QUEUE_FLUSH,null,"rayone") }
                } catch(e:Exception){ runOnUiThread { output.text=e.message } }
            }
        }
    }
    override fun onDestroy(){ recognizer?.destroy(); tts?.shutdown(); super.onDestroy() }
    private fun request(path:String, method:String, body:String):String {
        val c=URL(baseUrl+path).openConnection() as HttpURLConnection
        c.requestMethod=method; c.setRequestProperty("Content-Type","application/json")
        if(token.isNotEmpty()) c.setRequestProperty("Authorization","Bearer $token")
        if(method!="GET"){ c.doOutput=true; c.outputStream.use{it.write(body.toByteArray())} }
        val stream=if(c.responseCode<400)c.inputStream else c.errorStream
        return stream.bufferedReader().use{it.readText()}
    }
}
