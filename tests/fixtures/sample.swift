import Foundation

public protocol Greeter {
    func greet(name: String) -> String
}

struct Person {
    let name: String

    func greet(name: String) -> String {
        return "Hello, \(name)"
    }
}

class Service {
    func run(count: Int) async throws -> Void {}
}

enum Status {
    case ready
}

extension Person {
    func display() {}
}
