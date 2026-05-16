import 'dart:io';
import 'dart:convert' as convert;
import 'package:flutter/material.dart';
export 'package:flutter/widgets.dart';

abstract class Animal {
  void speak();
  String get name;
}

class Dog extends Animal {
  String name;
  Dog(this.name);

  @override
  void speak() {
    print('Woof!');
  }

  static Dog create(String name) {
    return Dog(name);
  }
}

mixin Flyable {
  void fly() {
    print('Flying!');
  }
}

enum Color { red, green, blue }

extension StringExt on String {
  String reverse() {
    return split('').reversed.join('');
  }
}

void topLevel(String msg) {
  print(msg);
}

int add(int a, int b) => a + b;
