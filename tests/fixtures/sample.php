<?php
namespace App\Models;

use App\Http\Controllers\Controller;
use Illuminate\Database\Eloquent\Model;
use App\Contracts\Renderable;

require_once 'config.php';
require 'database.php';

interface Renderable {
    public function render(): string;
}

trait HasTimestamps {
    public function setCreatedAt(string $date): void;
}

enum Status: string {
    case Active = 'active';
    case Inactive = 'inactive';
}

abstract class BaseUser extends Model implements Renderable {
    protected string $name;

    public function __construct(string $name) {
        $this->name = $name;
    }

    public function getName(): string {
        return $this->name;
    }

    abstract public function getRole(): string;
}

class AdminUser extends BaseUser {
    use HasTimestamps;

    public function getRole(): string {
        return 'admin';
    }

    public function render(): string {
        return 'Admin: ' . $this->name;
    }

    public static function create(string $name): self {
        return new self($name);
    }
}

function createUser(string $name): BaseUser {
    return new AdminUser($name);
}

function processData(array $items, callable $fn): array {
    return array_map($fn, $items);
}
