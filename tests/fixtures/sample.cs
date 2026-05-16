using System;
using System.Collections.Generic;

namespace ExampleApp
{
    public interface IGreeter
    {
        string Greet(string name);
    }

    public class Greeter : IGreeter
    {
        private readonly string _prefix;

        public Greeter(string prefix)
        {
            _prefix = prefix;
        }

        public string Greet(string name)
        {
            return $"{_prefix}, {name}!";
        }
    }

    public struct Point
    {
        public double X { get; set; }
        public double Y { get; set; }

        public double DistanceTo(Point other)
        {
            double dx = X - other.X;
            double dy = Y - other.Y;
            return Math.Sqrt(dx * dx + dy * dy);
        }
    }

    public enum Color
    {
        Red,
        Green,
        Blue
    }

    public static class MathHelper
    {
        public static int Add(int a, int b)
        {
            return a + b;
        }

        public static int Multiply(int a, int b)
        {
            return a * b;
        }
    }
}
