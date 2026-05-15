package sample.app

import kotlin.collections.List
import java.time.Instant as TimeInstant

interface Greeter {
    fun greet(name: String): String
}

data class User(val id: String, val name: String) : Greeter {
    override fun greet(name: String): String {
        return "Hello, $name"
    }
}

sealed class Result

object Registry {
    fun lookup(id: String): User? = null
}

fun topLevel(count: Int = 0): Unit {}
