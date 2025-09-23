#include <iostream>

int main() {
    // Declare variables to hold the numbers
    double num1, num2, sum;

    // Ask the user for input
    std::cout << "Enter the first number: ";
    std::cin >> num1;

    std::cout << "Enter the second number: ";
    std::cin >> num2;

    // Calculate the sum
    sum = num1 + num2;

    // Display the result
    std::cout << "The sum of " << num1 << " and " << num2 << " is " << sum << std::endl;

    return 0;
}