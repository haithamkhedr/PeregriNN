from pulp import *
from random import random,seed
from time import time
sys.path.append('./z3/z3-4.4.1-x64-osx-10.11/bin/')
import pickle
from Workspace import Workspace
import math
from gurobipy import * 
import sys
import os
from NeuralNetwork import *
from copy import copy
eps = 1E-5

class Solver():

    def __init__(self, network = None, maxIter = 100000):
        self.maxNumberOfIterations = maxIter
        self.nn        = network

        #TODO: self.__parse_network() #compute the dims of input and hidden nodes
        self.__input_dim    = self.nn.image_size
        self.__hidden_units = self.nn.num_hidden_neurons
        self.__output_dim   = self.nn.output_size
        self.num_layers     = self.nn.num_layers #including the input/output layers

        self.model = Model()
        self.model.params.OutputFlag = 0
        self.model.params.DualReductions = 0
        #Add variables
        self.state_vars         = self.model.addVars(self.__input_dim,name = "x", lb  = -1*GRB.INFINITY)  
        self.relu_vars           = self.model.addVars(self.__input_dim,name = "y", lb = -1*GRB.INFINITY)      
        self.relu_vars.update(self.model.addVars([self.__input_dim + i for i in range(self.__hidden_units)],name = "y", lb = 0))
        self.net_vars        = self.model.addVars(self.__input_dim,name = "n",lb = -1* GRB.INFINITY)      
        self.net_vars.update(self.model.addVars([self.__input_dim + i for i in range(self.__hidden_units)],name = "n", lb = -1* GRB.INFINITY) )
        self.slack_vars         = self.model.addVars(self.__input_dim + self.__hidden_units,name = "s",lb = 0)
        self.out_vars           = self.model.addVars(self.__output_dim,name = "u", lb = -1* GRB.INFINITY)
        #Layer index 
        self.model.update()
        self.layer_start_idx = [0] * len(self.nn.layers)
        for layer_idx, _ in self.nn.layers.items():
            if(layer_idx == 0):
                continue
            self.layer_start_idx[layer_idx] = self.layer_start_idx[layer_idx-1] + self.nn.layers[layer_idx-1]['num_nodes']
        self.linear_constraints = []

    def add_linear_constraints(self, A, x, b, sense = GRB.LESS_EQUAL):
        #Senses are GRB.LESS_EQUAL, GRB.EQUAL, or GRB.GREATER_EQUAL
        for row in range(len(b)):
            linear_expression = LinExpr(A[row],x)
            constraint = {'expr' : linear_expression, 'sense': sense,'rhs': b[row]} 
            self.linear_constraints.append(constraint)

    def __add_NN_constraints(self):

        fixed_relus = 0
        #First layer of network is assumed to be the input to the network
        layer_idx = 0
        num_neurons = self.nn.layers[layer_idx]['num_nodes']
        layer_start_idx = self.layer_start_idx[layer_idx]
        for neuron_idx in range(num_neurons):
            neuron_abs_idx = layer_start_idx + neuron_idx
            self.model.addConstr(self.relu_vars[neuron_abs_idx] == self.state_vars[neuron_abs_idx])
            self.model.addConstr(self.net_vars[neuron_abs_idx] == self.state_vars[neuron_abs_idx])
        for layer_idx in range(1,self.num_layers): #exclude input
            num_neurons = self.nn.layers[layer_idx]['num_nodes']
            layer_start_idx = self.layer_start_idx[layer_idx]
            prev_layer_start_idx = self.layer_start_idx[layer_idx - 1]
            W = self.nn.layers[layer_idx]['weights']
            b = self.nn.layers[layer_idx]['bias']
            ub = self.nn.layers[layer_idx]['sym_ub']
            lb = self.nn.layers[layer_idx]['sym_lb']
            prev_layer_size = self.nn.layers_sizes[layer_idx -1]
            for neuron_idx in range(num_neurons):
                #add - constraints
                neuron_abs_idx = layer_start_idx + neuron_idx
                net_expr = LinExpr(W[neuron_idx], [self.relu_vars[prev_layer_start_idx + input_idx] for input_idx in range(prev_layer_size)])
                if(self.nn.layers[layer_idx]['type'] != 'output'):
                    self.model.addConstr(self.net_vars[neuron_abs_idx] == (net_expr + b[neuron_idx]))
                    self.model.addConstr(self.slack_vars[neuron_abs_idx] == self.relu_vars[neuron_abs_idx] - self.net_vars[neuron_abs_idx])
                    
                    if(ub[neuron_idx] <= 0):
                        self.model.addConstr(self.relu_vars[neuron_abs_idx] == 0)
                        fixed_relus +=1

                    elif(lb[neuron_idx] >= 0):
                        self.model.addConstr(self.slack_vars[neuron_abs_idx] == 0)
                        fixed_relus +=1
                    
                    else:
                        factor = (ub[neuron_idx]/ (ub[neuron_idx]-lb[neuron_idx]))[0]
                        self.model.addConstr(self.relu_vars[neuron_abs_idx] <= factor * (self.net_vars[neuron_abs_idx]- lb[neuron_idx]))

                else:
                    self.model.addConstr(self.out_vars[neuron_idx] == (net_expr + b[neuron_idx]))
                    self.model.addConstr(self.out_vars[neuron_idx] >= lb[neuron_idx])
                    self.model.addConstr(self.out_vars[neuron_idx] <= ub[neuron_idx])

        print('Number of fixed Relus:', fixed_relus)
    
    def solve(self):
        
        status = 'TLE'
        solutionFound = False
        iterationsCounter = -1
        counter_examples = []
        while solutionFound == False and iterationsCounter < self.maxNumberOfIterations:
            iterationsCounter               = iterationsCounter + 1

            if iterationsCounter % 100 == 0:
                # self.print_lock.acquire()
                print('******** Solver , iteration = ', iterationsCounter ,'********')
                # self.print_lock.release()

            self.__prepare_problem()
            self.model.write('model.lp')
            self.model.optimize()
            if(self.model.Status == 3): #Infeasible
                IIS_slack = []
                try:
                    self.model.computeIIS() 
                    fname = 'result.ilp'
                    self.model.write(fname)
                except Exception as e:
                    print(e)
                status = 'UNSAT'
                return None,None,status
            else:   
                status = 'UNKNOWN'
                SAT,infeasible_relus = self.check_SAT() 
                solutionFound = True
                if(SAT):
                    print('Solution found')
                    x = [self.model.getVarByName('x[%d]'%i).X for i in range(len(self.state_vars))]
                    u = [self.model.getVarByName('u[%d]'%i).X for i in range(len(self.out_vars))]
                    print('x',x)
                    print('u',u)
                    status = 'SolFound'  
                    return x,u,status
                else:
                    status = 'UNKNOWN'
                    status = self.dfs(infeasible_relus,[])
                    print(status)

        
        return self.model.getVars(),counter_examples,status
    def fix_relu(self, relu_idx, phase, fixed_relus):

        if(phase == 1):
            self.model.addConstr(self.slack_vars[relu_idx] == 0,name="active_"+str(relu_idx))
        else:
            self.model.addConstr(self.relu_vars[relu_idx] == 0,name="inactive_"+str(relu_idx))
            self.add_objective(fixed_relus)



    def dfs(self, infeasible_relus,fixed_relus):
        #node to be handled
        status = 'UNKNOWN'
        relu_idx,phase =  infeasible_relus[0]
        # print(relu_idx,'Active')
        #set this relu to active
        fixed_relus.append(relu_idx)
        self.fix_relu(relu_idx,phase,fixed_relus)
        self.model.optimize()
        if(self.model.Status == 2): #Feasible solution
            SAT,infeasible_set = self.check_SAT()
            if(SAT):
                print('Solution found')
                status = 'SolFound'  
            else:
                status = self.dfs(infeasible_set,copy(fixed_relus))

        if(status != 'SolFound'):
            #infeasible solution
            #set the neuron to other phase
            if(phase == 1):
                self.model.remove(self.model.getConstrByName("active_"+str(relu_idx)))
                phase = 0
            else:
                self.model.remove(self.model.getConstrByName("inactive_"+str(relu_idx)))
                phase  = 1

            self.fix_relu(relu_idx, phase,fixed_relus)
            self.model.optimize()
            if(self.model.Status == 2): #Feasible solution
                SAT,infeasible_set = self.check_SAT()
                if(SAT):
                    print('Solution found')
                    status = 'SolFound'  
                else:
                    status = self.dfs(infeasible_set,copy(fixed_relus))
            if(status != 'SolFound'):
                status = 'UNSAT'

            #clear constraints of this neuron
            if(phase == 1):
                self.model.remove(self.model.getConstrByName("active_"+str(relu_idx)))
            else:
                self.model.remove(self.model.getConstrByName("inactive_"+str(relu_idx)))

        
        return status
            



    def check_SAT(self):   
        for layer_idx in range(1,self.num_layers-1): #exclude input
            num_neurons = self.nn.layers[layer_idx]['num_nodes']
            layer_start_idx = self.layer_start_idx[layer_idx]
            y = np.array([self.model.getVarByName('y[%d]'%idx).X for idx in range(layer_start_idx, layer_start_idx + num_neurons)])
            net = np.array([self.model.getVarByName('n[%d]'%idx).X for idx in range(layer_start_idx, layer_start_idx + num_neurons)])
            active_feas = ((y-net) > eps) * (net > eps) #if y>net in net>0 domain
            inactive_feas =  ((y > eps) * (net < eps))    #if y > 0 in net<0 domain
            layer_sat = not (np.any(active_feas) or np.any(inactive_feas))
            if(layer_sat == False):
                active = list(np.where(active_feas == True)[0]+ layer_start_idx)
                inactive = list(np.where(inactive_feas == True)[0] + layer_start_idx)
                infeas_relus =  [(n_idx,1) for n_idx in active]
                infeas_relus += [(n_idx,0) for n_idx in inactive]
                return False,infeas_relus
        return True,[]
    
    def __prepare_problem(self):
        #clear all constraints
        self.model.remove(self.model.getConstrs())
        #Add external convex constraints
        for constraint in self.linear_constraints:
            self.model.addConstr(constraint['expr'], sense = constraint['sense'], rhs = constraint['rhs'])

        self.__add_NN_constraints()
        self.add_objective()

    

    def add_objective(self, fixed_relus = None):
        slacks = self.slack_vars.values()[self.__input_dim:]
        relus  = self.relu_vars.values()[self.__input_dim:]
        slack_strt_idx = 0
        init_weight = 1E10
        weights = []
        for layer_idx,_ in enumerate(self.nn.layers_sizes[1:-1]):
            ub = np.maximum(0,self.nn.layers[layer_idx+1]['ub'])
            ub[ub > 0] = 1
            weights += list(init_weight * ub)
            # weights += [1] * layer_size
            init_weight /= 100

        obj = LinExpr()
        if(fixed_relus):
            for idx in fixed_relus:
                weights[idx - self.__input_dim] = 0

        obj.addTerms(weights,slacks)
        self.model.setObjective(obj)
        self.model.update()


        
    
# layers_sizes = [2,3,1]
# image_size = layers_sizes[0]
# x = np.zeros((2,1))
# bounds = np.concatenate((x,x),axis = 1)
# nn = NeuralNetworkStruct(layers_sizes,input_bounds = bounds)
# solver = Solver(network = nn)
# A = np.eye(2)
# b = np.zeros(2)
# state_vars = [solver.state_vars[0],solver.state_vars[1]]
# solver.add_linear_constraints(A,state_vars,b,LpConstraintEQ)
# A = [[1, 0], [-1, 0], [0, 1], [0, -1]]
# b = [1,-0.1,1,-0.1]
# state_vars = [solver.state_vars[0],solver.state_vars[1]]
# solver.add_linear_constraints(A,state_vars,b)
# state_vars = [solver.out_vars[0]]
# A, b = [[-1]],[-0.1]
# solver.add_linear_constraints(A, state_vars, b)
# solver.solve()

# e = 0.1
# layers_sizes = [1,2,1]
# image_size = layers_sizes[0]
# bounds = np.zeros((1,2))
# bounds[:,1] = 1
# nn = NeuralNetworkStruct(layers_sizes,input_bounds = bounds)
# Weights= [np.concatenate((np.array([-1]),np.array([1])),axis = 0).reshape((2,1))]
# Weights.append(np.concatenate((np.array([[1],[1]])),axis = 0).reshape((1,2)))
# biases = [np.array([e,e-1]),np.zeros(2)]
# nn.set_weights(Weights,biases)
# solver = Solver(network = nn)
# state_vars = [solver.state_vars[0]]
# A, b = [[1],[-1]],[1,0]
# solver.add_linear_constraints(A, state_vars, b)
# state_vars = [solver.out_vars[0]]
# A, b = [[1],[-1]],[e,-e/2]
# solver.add_linear_constraints(A, state_vars, b)
# solver.solve()