from solver import *
from time import time,sleep
from random import random, seed
import numpy as np
import signal
import sys,os
import glob
from NeuralNetwork import *

eps = 5E-1



def check_property(x):
    u = nn.evaluate(x)
    if(np.argmax(u) != target):
        print("Potential CE succeeded")
        return True
    return False

if __name__ == "__main__":

    #init Neural network
    nnet = 'nnet/mnist24.nnet'
    nn = NeuralNetworkStruct()
    nn.parse_network(nnet,type = 'mnist')
    print('Loaded network:',nnet)

    # image_files = sorted(glob.glob('images/*'))
    image_files = ['images/image%d'%idx for idx in range(100)]
    deltas = [5.0]
    num_test = 5
    for image_file in image_files[:num_test]:
        with open(image_file,'r') as f:
            image = f.readline().split(',')
            image = np.array([float(num) for num in image[:-1]]).reshape((-1,1))
            output = nn.evaluate(image)
            target = np.argmax(output)
            other_ouputs = [i for i in range(nn.output_size) if i != target]
            print('Testing',image_file)
            print('Output:',output,'\nTarget-->',target)
        for delta in deltas:
            print('Norm:',delta)
            #Solve the problem for each other output
            start_time = time()
            adv_found = False
            for out_idx in other_ouputs:
                network = deepcopy(nn)
                lb = np.maximum(image-delta,0)
                ub = np.minimum(image+delta,255)
                input_bounds = np.concatenate((lb,ub),axis = 1)
                network.set_bounds(input_bounds)
                solver = Solver(network = network,property_check=check_property,target = target)
                #Add Input bounds as constraints in the SAT solver
                #TODO: Make the solver apply the bound directly from the NN object
                input_vars = [solver.state_vars[i] for i in range(len(solver.state_vars))]
                A = np.eye(network.image_size)
                lower_bound = input_bounds[:,0]
                upper_bound = input_bounds[:,1]
                solver.add_linear_constraints(A,input_vars,lower_bound,GRB.GREATER_EQUAL)
                solver.add_linear_constraints(A,input_vars,upper_bound,GRB.LESS_EQUAL)

                # A = np.eye(len(solver.state_vars))
                # b = [-0.277091,0.173774,0.515735,0.978737,0.684880]
                # b = [-0.258785, 0.143822, 0.148294,0.50000,0.477025]
                # b = [-0.100000,-0.025285,0.011807,-0.009691,-0.100000]
                # solver.add_linear_constraints(A,input_vars,b,GRB.EQUAL)

                # output_vars = [solver.out_vars[i] for i in range(len(solver.out_vars))]
                # A = np.eye(len(solver.out_vars))
                # b = [-0.0162349481, -0.0180076580, -0.0178982665, -0.0178564177, -0.0174600866]
                # solver.add_linear_constraints(A,output_vars,b,GRB.EQUAL)

                output_vars = [solver.out_vars[i] for i in range(len(solver.out_vars))]
                A = np.zeros(network.output_size)
                A[out_idx] = 1
                A[target] = -1
                b = [eps]
                solver.add_linear_constraints([A],output_vars,b,GRB.GREATER_EQUAL)
                
                solver.preprocessing = False
                s = time()
                nn_in,nn_out,status = solver.solve()
                e = time()
                if(status == 'SolFound'):
                    adv_found = True
                    nn_in = np.array([solver.state_vars[idx].X for idx in range(network.image_size)]).reshape((-1,1))
                    nn_out = np.array([solver.out_vars[idx].X for idx in range(network.output_size)]).reshape((-1,1))
                    net_out = network.evaluate(nn_in)
                    err = np.sum(np.fabs(network.evaluate(nn_in) - nn_out))
                    # print(nn_in)
                    print(net_out)
                    print('Adversarial example found with label %d ,delta %f'%(out_idx,delta))
                    # print('Error',err)
                    print('Total time:',time() - start_time)
                    break
                # else:
                #     print("Problem Infeasible,Time:%f"%(e-s))

            if(not adv_found):
                print('Problem is Infeasible, Total time:%f\n\n'%(time() - start_time))



        

#active neurons [ 3  4  6  7  9 12 15 20 22 23 25 27 28 30 32 33 34 40 45 49]

