package sample.app

import scala.collection.mutable.ListBuffer
import java.time.Instant
import scala.util.{Try, Success, Failure}

trait Greeter {
  def greet(name: String): String
}

case class User(id: String, name: String) extends Greeter {
  override def greet(name: String): String = {
    s"Hello, $name"
  }
}

sealed trait Result
sealed class Error(msg: String)

object Registry {
  def lookup(id: String): Option[User] = None
}

class Service {
  def run(count: Int): Unit = {}
}

def topLevel(x: Int): Int = x + 1
